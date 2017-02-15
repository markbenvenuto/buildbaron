#!/usr/bin/env python3
"""
Script to analyze the Jira Build Baron Queue
"""
import argparse
import binascii
import datetime
import dateutil
import dateutil.relativedelta
import hashlib
import json
import os
import pprint
import re
import stat
import sys

# Global override
UPDATE_JIRA = False

if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(os.path.realpath(__file__)))))
    print(sys.path)

import buildbaron.analyzer.analyzer_config
import buildbaron.analyzer.evergreen
import buildbaron.analyzer.evg_log_file_analyzer
import buildbaron.analyzer.extract_backtrace
import buildbaron.analyzer.jira_client
import buildbaron.analyzer.log_file_analyzer
import buildbaron.analyzer.logkeeper
import buildbaron.analyzer.parallel_failure_analyzer
import buildbaron.analyzer.timeout_file_analyzer


# URL of the default Jira server.
# If you use .com, it breaks horribly
def ParseJiraTicket(issue, summary, description):
    # Parse summary
    if "System Failure:" in summary:
        type = "system_failure"
    elif "Timed Out:" in summary:
        type = "timed_out"
    elif "Failures" in summary:
        type = "test_failure"
    elif "Failure" in summary:
        type = "test_failure"
    elif "Failed" in summary:
        type = "task_failure"
    else:
        raise ValueError("Unknown summary " + str(summary))

    suite, build_variant, project, githash = ("unknown", "unknown", "unknown", "unknown")
    summary_match = re.match(".*?: (.*) on (.*) \[(.*) @ ([a-zA-Z0-9]+)\]", summary)
    if summary_match:
        suite, build_variant, project, githash = summary_match.groups()

    # Parse Body of description
    lines = description.split("\n")
    tests = []
    for line in lines:
        if line.startswith('h2.'):
            url_match = re.search("\|(.*)\]", line)
            task_url = url_match.group(1)
        elif "[Logs|" in line:
            log_line_match = re.match("\*(.*)\* - \[Logs\|(.*?)\]", line)
            if log_line_match:
                test_name = log_line_match.group(1)
                log_file = log_line_match.group(2)
                tests.append({'name': test_name, 'log_file': log_file})
        else:
            pass

    return bfg_fault_description(issue,
                                 summary,
                                 type,
                                 project,
                                 githash,
                                 task_url,
                                 suite,
                                 build_variant,
                                 tests)


class bfg_fault_description:
    """Parse a fault description into type"""

    def __init__(self,
                 issue,
                 summary,
                 type,
                 project,
                 githash,
                 task_url,
                 suite,
                 build_variant,
                 tests):
        self.issue = issue
        self.summary = summary
        self.type = type
        self.project = project
        self.githash = githash
        self.task_url = task_url
        self.suite = suite
        self.build_variant = build_variant
        self.tests = tests

    def to_json(self):
        return json.dumps(self, cls=BFGCustomEncoder)


class BFGCustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bfg_fault_description):
            return {
                "issue": obj.issue,
                "summary": obj.summary,
                "type": obj.type,
                "task_url": obj.task_url,
                "project": obj.project,
                "githash": obj.githash,
                "suite": obj.suite,
                "build_variant": obj.build_variant,
                "tests": obj.tests
            }

        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


class BFGCustomDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        if 'task_url' not in obj and "project" not in obj:
            return obj

        return bfg_fault_description(obj['issue'], obj['summary'], obj['type'], obj['project'],
                                     obj['task_url'], obj['suite'], obj['build_variant'],
                                     obj['tests'])


class bfg_analyzer(object):
    """description of class"""

    def __init__(self, jira_client):
        self.jira_client = jira_client
        self.evg_client = buildbaron.analyzer.evergreen.client()
        self.pp = pprint.PrettyPrinter()

    def query(self, query_str):
        #results = self.jira_client.search_issues("project = bfg AND resolution is EMPTY AND created > 2017-01-25 AND created <= 2017-02-01 and summary ~ Timed ORDER BY created DESC", maxResults=25)
        results = self.jira_client.search_issues(query_str, maxResults=100)

        print("Result Count %d" % len(results))

        bfs = []

        for result in results:
            bfs.append(ParseJiraTicket(
                result.key,
                result.fields.summary,
                result.fields.description
            ))

        # Save to disk to help investigation of bad results
        bfs_str = json.dumps(bfs, cls=BFGCustomEncoder, indent="\t")
        with open("bfs.json", "wb") as sjh:
            sjh.write(bfs_str.encode())

        # Return a list of dictionaries instead of a list of bfg_fault_description
        return json.loads(bfs_str)

    def check_logs(self, bfs):
        results = []

        for bf in bfs:
            self.process_bf(bf, results)
            jira_issue = self.jira_client.get_bfg_issue(bf["issue"])
            jira_issue.fields.labels.append("bot-analyzed")
            print("LABELS: " + str(jira_issue.fields.labels))
            jira_issue.add_field_value("labels", "bot-analyzed")

        return results

    # TODO: parallelize the check_logs function with this since we are network bound
    # builds = thread_map( lambda item : process_bf(base_url, item), commits)
    def thread_map(func, items):
        # We can use a with statement to ensure threads are cleaned up promptly
        with concurrent.futures.ThreadPoolExecutor(max_workers=cpu_count() * 2) as executor:
            # Start the load operations and mark each future with its URL
            future_to_item = {executor.submit(func, item): item for item in items}
            results = []
            for future in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[future]
                try:
                    nf = future.result()
                    if nf:
                        results += nf
                except Exception as exc:
                    print('%r generated an exception: %s' % (item, exc))
        return results

    def create_bf_cache(self, bf):
        """Create a directory to cache the log file in"""
        if not os.path.exists("cache"):
            os.mkdir("cache")
        if not os.path.exists(os.path.join("cache", "bf")):
            os.mkdir(os.path.join("cache", "bf"))

        m = hashlib.sha1()
        m.update(bf["task_url"].encode())
        digest = m.digest()
        digest64 = binascii.b2a_hex(digest).decode()
        bf["hash"] = digest64
        path = os.path.join("cache", "bf", digest64)
        bf["bf_cache"] = path

        if not os.path.exists(path):
            os.mkdir(path)

    def create_test_cache(self, bf, test):
        """Create a directory to cache the log file in"""

        m = hashlib.sha1()
        m.update(test["name"].encode())
        digest = m.digest()
        digest64 = binascii.b2a_hex(digest).decode()
        test["hash"] = digest64
        path = os.path.join(bf['bf_cache'], digest64)
        test["cache"] = path

        if not os.path.exists(path):
            os.mkdir(path)

    def process_bf(self, bf, results):
        """Process a log through the log file analyzer

        Saves test information in cache\XXX\test.json
        Saves analysis information in cache\XXX\summary.json
        """
        self.create_bf_cache(bf)

        print("BF: " + str(bf))

        system_log_url = buildbaron.analyzer.evergreen.task_get_system_raw_log(bf['task_url'])
        task_log_file_url = buildbaron.analyzer.evergreen.task_get_task_raw_log(bf["task_url"])

        bf['system_log_url'] = system_log_url
        bf['task_log_file_url'] = task_log_file_url

        # Handle normal test failures
        if bf['type'] == 'test_failure':
            # Go through each test
            for test in bf['tests']:
                self.process_test(bf, test, results)
        elif bf['type'] == 'system_failure':
            self.process_system_failure(bf, results)
        elif bf['type'] == 'task_failure':
            self.process_task_failure(bf, results)
        elif bf['type'] == 'timed_out':
            self.process_time_out(bf, results)

        print("")

    def process_system_failure(self, bf, results):
        cache_dir = bf["bf_cache"]
        log_file = os.path.join(cache_dir, "test.log")
        summary_json = os.path.join(cache_dir, "summary.json")

        bf['log_file_url'] = bf['task_log_file_url']
        bf['name'] = 'task'
        bf['cache'] = bf['bf_cache']

        if os.path.exists(summary_json) and not UPDATE_JIRA:
            with open(summary_json, "rb") as sjh:
                contents = sjh.read().decode('utf-8')
                summary_str = json.loads(contents)

            results.append({"test": bf, "summary": summary_str})
            return

        if not os.path.exists(log_file):
            self.evg_client.retrieve_file(bf['task_log_file_url'], log_file)

        with open(log_file, "rb") as lfh:
            log_file_str = lfh.read().decode('utf-8')

        analyzer = buildbaron.analyzer.evg_log_file_analyzer.EvgLogFileAnalyzer(log_file_str)

        analyzer.analyze()

        faults = analyzer.get_faults()

        if len(faults) == 0:
            print("===========================")
            print("Analysis failed for test: " + self.pp.pformat(bf))
            print("To Debug: python analyzer" + os.path.sep + "log_file_analyzer.py " + log_file)
            print("===========================")
        else:
            self.add_system_failure_comment(bf, bf['task_log_file_url'], faults)

        for f in analyzer.get_faults():
            buildbaron.analyzer.extract_backtrace.add_fault_comment(
                self.jira_client, bf["issue"], bf["githash"], f)

        summary_str = analyzer.to_json()
        summary_obj = json.loads(summary_str)

        with open(summary_json, "wb") as sjh:
            sjh.write(summary_str.encode())

        results.append({"test": bf, "summary": summary_obj})

    def process_task_failure(self, bf, results):
        cache_dir = bf["bf_cache"]
        log_file = os.path.join(cache_dir, "test.log")
        summary_json = os.path.join(cache_dir, "summary.json")

        bf['log_file_url'] = bf['task_log_file_url']
        bf['name'] = 'task'
        bf['cache'] = bf['bf_cache']

        if os.path.exists(summary_json):
            with open(summary_json, "rb") as sjh:
                contents = sjh.read().decode('utf-8')
                summary_str = json.loads(contents)

            results.append({"test": bf, "summary": summary_str})
            return

        if not os.path.exists(log_file):
            self.evg_client.retrieve_file(bf['task_log_file_url'], log_file)

        with open(log_file, "rb") as lfh:
            log_file_str = lfh.read().decode('utf-8')

        analyzer = buildbaron.analyzer.evg_log_file_analyzer.EvgLogFileAnalyzer(log_file_str)

        analyzer.analyze()

        faults = analyzer.get_faults()

        if len(faults) == 0:
            oom_analyzer = self.check_for_oom_killer(bf)
            if oom_analyzer is None:
                print("===========================")
                print("Analysis failed for test: " + self.pp.pformat(bf))
                print("To Debug: python analyzer" + os.path.sep + "log_file_analyzer.py " +
                      log_file)
                print("===========================")
            else:
                analyzer = oom_analyzer
        else:
            pass
            #  self.add_system_failure_comment(bf, log_file_url, faults)

        for f in analyzer.get_faults():
            buildbaron.analyzer.extract_backtrace.add_fault_comment(
                self.jira_client,
                bf["issue"],
                bf["githash"],
                f)

        summary_str = analyzer.to_json()
        summary_obj = json.loads(summary_str)

        with open(summary_json, "wb") as sjh:
            sjh.write(summary_str.encode())

        results.append({"test": bf, "summary": summary_obj})

    def process_time_out(self, bf, results):
        cache_dir = bf["bf_cache"]
        log_file = os.path.join(cache_dir, "test.log")
        summary_json = os.path.join(cache_dir, "summary.json")

        bf['log_file_url'] = bf['task_log_file_url']
        bf['name'] = 'task'
        bf['cache'] = bf['bf_cache']

        if os.path.exists(summary_json):
            with open(summary_json, "rb") as sjh:
                contents = sjh.read().decode('utf-8')
                summary_str = json.loads(contents)

            results.append({"test": bf, "summary": summary_str})
            return

        if not os.path.exists(log_file):
            self.evg_client.retrieve_file(bf['task_log_file_url'], log_file)

        with open(log_file, "rb") as lfh:
            log_file_str = lfh.read().decode('utf-8')

        print("Checking " + log_file)
        analyzer = buildbaron.analyzer.timeout_file_analyzer.TimeOutAnalyzer(log_file_str)

        analyzer.analyze()

        for fault in analyzer.get_faults():
            buildbaron.analyzer.extract_backtrace.add_fault_comment(
                self.jira_client,
                bf["issue"],
                bf["githash"],
                fault)

        incomplete_tests = analyzer.get_incomplete_tests()
        for incomplete_test in incomplete_tests:
            jira_issue = self.jira_client.get_bfg_issue(bf["issue"])
            timeout_comment = (
                "*" +
                incomplete_test["name"] +
                " timed out* - [Logs|" +
                incomplete_test["log_file"] +
                "]"
            )
            try:
                if "bot-analyzed" not in jira_issue.fields.labels:
                    jira_issue.update(
                        description=jira_issue.fields.description +
                        "\n{0}\n".format(timeout_comment))
            except buildbaron.analyzer.jira_client.JIRAError as e:
                print("Error updating jira: " + str(e))

        if len(incomplete_tests) == 0:
            faults = analyzer.get_faults()

            if len(faults) == 0:
                print("===========================")
                print("Analysis failed for test: " + self.pp.pformat(bf))
                print("To Debug: python analyzer" + os.path.sep + "timeout_file_analyzer.py " +
                      log_file)
                print("===========================")

            summary_str = analyzer.to_json()
            summary_obj = json.loads(summary_str)

            with open(summary_json, "wb") as sjh:
                sjh.write(summary_str.encode())

            results.append({"test": bf, "summary": summary_obj})

        else:
            for incomplete in incomplete_tests:
                self.process_test(bf, incomplete, results)
            bf['tests'] = incomplete_tests

    def process_test(self, bf, test, results):
        self.create_test_cache(bf, test)

        cache_dir = test["cache"]
        log_file = os.path.join(cache_dir, "test.log")
        summary_json = os.path.join(cache_dir, "summary.json")

        nested_test = test
        for key in bf.keys():
            if key != 'tests' and key != 'name':
                nested_test[key] = bf[key]

        faults = []

        oom_analyzer = self.check_for_oom_killer(bf)
        if oom_analyzer:
            faults.extend(oom_analyzer.get_faults())

        # If logkeeper is down, we will not have a log file :-(
        if test["log_file"] is not None and test["log_file"] != "" and "test/None" not in test[
                "log_file"] and "log url not available" not in test["log_file"]:

            if not os.path.exists(log_file):
                buildbaron.analyzer.logkeeper.retieve_raw_log(test["log_file"], log_file)

            test["log_file_url"] = buildbaron.analyzer.logkeeper.get_raw_log_url(
                test["log_file"])

            log_file_stat = os.stat(log_file)

            if log_file_stat[stat.ST_SIZE] > 50 * 1024 * 1024:
                summary_str = "Skipping Large File : " + str(log_file_stat[stat.ST_SIZE])
                results.append({"test": nested_test, "summary": summary_str})
                return
        else:
            test["log_file_url"] = "none"
            with open(log_file, "wb") as lfh:
                lfh.write("Logkeeper was down\n".encode())

            log_file_stat = os.stat(log_file)

        if log_file_stat[stat.ST_SIZE] > 50 * 1024 * 1024:
            print("Skipping Large File : " + str(log_file_stat[stat.ST_SIZE]) + " at " + str(
                log_file))
            print(summary_str)
            summary_obj = summary_str
        else:
            with open(log_file, "rb") as lfh:
                log_file_str = lfh.read().decode('utf-8')

        print("Checking Log File")
        LFS = buildbaron.analyzer.log_file_analyzer.LogFileSplitter(log_file_str)
        analyzer = buildbaron.analyzer.log_file_analyzer.LogFileAnalyzer(LFS.get_streams())

        analyzer.analyze()

        faults.extend(analyzer.get_faults())

        if test["name"].startswith("basic") and test["name"].endswith(".js"):
            print("Anlyzing basic.js or basicPlus.js failure")
            parallel_analyzer = \
                buildbaron.analyzer.parallel_failure_analyzer.ParallelTestFailureAnalyzer(
                    log_file_str)
            parallel_analyzer.analyze()
            faults.extend(parallel_analyzer.get_faults())

        if len(faults) == 0:
            print("===========================")
            print("Analysis failed for test: " + self.pp.pformat(bf))
            print("To Debug: python analyzer" + os.path.sep + "log_file_analyzer.py " +
                  log_file)
            print("===========================")

        for fault in faults:
            buildbaron.analyzer.extract_backtrace.add_fault_comment(
                self.jira_client,
                bf["issue"],
                bf["githash"],
                fault)

        if os.path.exists(summary_json):
            with open(summary_json, "rb") as sjh:
                contents = sjh.read().decode('utf-8')
                summary_str = json.loads(contents)

            results.append({"test": nested_test, "summary": summary_str})
            return

        summary_str = analyzer.to_json()
        summary_obj = json.loads(summary_str)

        with open(summary_json, "wb") as sjh:
            sjh.write(summary_str.encode())

        results.append({"test": nested_test, "summary": summary_obj})

    def check_for_oom_killer(self, bf):
        cache_dir = bf["bf_cache"]
        log_file = os.path.join(cache_dir, "test.log")

        if not os.path.exists(log_file):
            self.evg_client.retrieve_file(bf['system_log_url'], log_file)

        with open(log_file, "rb") as lfh:
            log_file_str = lfh.read().decode('utf-8')

        analyzer = buildbaron.analyzer.evg_log_file_analyzer.EvgLogFileAnalyzer(log_file_str)

        analyzer.analyze_oom()

        if len(analyzer.get_faults()) > 0:
            return analyzer

        return None

    def add_system_failure_comment(self, bf, log_file_url, faults):
        if UPDATE_JIRA:
            issue = self.jira_client.issue(bf['issue'])
            comments = issue.fields.comment.comments
        else:
            comments = []
        print("Comment count:" + str(len(comments)))

        if len(comments) == 0:
            # Add a comment with what we learned
            print("Reporting BF summary to Jira: %s - %s" % (str(bf), log_file_url))
            self.pp.pformat(faults)

            fault = faults[0]

            # TODO: move this to evergreen module
            log_file_url_line = log_file_url.replace("&text=true", "#L" + str(fault.line_number))
            message = """[Raw Log File|%s]
[Fault Details|%s]:
{noformat}
%s
{noformat}
""" % (log_file_url, log_file_url_line, fault.context)
            print(message)

            if UPDATE_JIRA:
                print("Updating Jira issue '%s'" % issue.key)
                self.jira_client.add_comment(issue.key, message)


def query_bfg_str(start, end):
    # Dates should be formatted as 2017-01-25
    return ('project = bfg'
            ' AND resolution is EMPTY'
            ' AND created > {createdStart}'
            ' AND created <= {createdEnd}'
            ' AND summary !~ "System Failure:"'
            ' ORDER BY created DESC'.format(
                createdStart=start.strftime("%Y-%m-%d"),
                createdEnd=end.strftime("%Y-%m-%d")))


def get_last_week_query():
    today = datetime.date.today()

    # The start of build baron - if today is Wednesday, returns prior Wednesday otherwise return
    # prior x2 Wednesday
    last_wednesday = today + dateutil.relativedelta.relativedelta(
        weekday=dateutil.relativedelta.WE(-2))

    # The end of build baron
    last_tuesday = today + dateutil.relativedelta.relativedelta(
        weekday=dateutil.relativedelta.TU(-1))

    return query_bfg_str(last_wednesday, last_tuesday)


def get_this_week_query():
    today = datetime.date.today()

    # The start of build baron - last Wednesday (or today if today is Wednesday)
    next_wednesday = today + dateutil.relativedelta.relativedelta(
        weekday=dateutil.relativedelta.WE(-1))

    # The end of build baron - this Wednesday
    this_tuesday = today + dateutil.relativedelta.relativedelta(
        weekday=dateutil.relativedelta.WE(1))

    return query_bfg_str(next_wednesday, this_tuesday)


def main():
    # tests1()

    parser = argparse.ArgumentParser(description='Analyze test failure in jira.')

    group = parser.add_argument_group("Jira options")
    group.add_argument(
        '--jira_server',
        type=str,
        help="Jira Server to query",
        default=buildbaron.analyzer.analyzer_config.jira_server())
    group.add_argument(
        '--jira_user',
        type=str,
        help="Jira user name",
        default=buildbaron.analyzer.analyzer_config.jira_user())

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '--last_week', action='store_true', help="Query of Last week's build baron queue")
    group.add_argument(
        '--this_week', action='store_true', help="Query of This week's build baron queue")
    group.add_argument('--query_str', type=str, help="Any query against implicitly the BFG project")

    args = parser.parse_args()

    if args.query_str:
        query_str = "(PROJECT = BFG) AND (%s)" % args.query_str
    elif args.last_week:
        query_str = get_last_week_query()
    else:
        query_str = get_this_week_query()

    print("Query: %s" % query_str)

    jira_client = buildbaron.analyzer.jira_client.jira_client(args.jira_server, args.jira_user)

    bfa = bfg_analyzer(jira_client)

    bfs = bfa.query(query_str)

    failed_bfs = bfa.check_logs(bfs)

    print("Total BFs to investigate %d\n" % len(failed_bfs))

    failed_bfs_root = {
        'query': query_str,
        'date': datetime.datetime.now().isoformat(' '),
        'bfs': failed_bfs
    }

    with open("failed_bfs.json", "w", encoding="utf8") as sjh:
        json.dump(failed_bfs_root, sjh, indent="\t")


if __name__ == '__main__':
    main()
