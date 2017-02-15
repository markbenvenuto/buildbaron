#!/usr/bin/env3 python
"""
TODO
"""

import argparse
import re
import requests

from . import analyzer_config
from . import jira_client
from . import log_file_analyzer
from . import faultinfo


def get_jira_client():
    print("Connecting to JIRA.....")
    return jira_client.jira_client(analyzer_config.jira_server(), analyzer_config.jira_user())


def build_backtraces(fault, gui_github_url_base, raw_github_url_base):
    """
    returns a list of strings representing a backtrace, as well as a parsed version represented as a
    list of objects of the form
    {
      "github_url": "https://github.com/mongodb/mongo/blob/deadbeef/jstests/core/test.js#L42",
      "first_line_number": 37,
      "line_number": 42,
      "file": "jstests/core/test.js",
      "file_name": "test.js",
      "lines": ["line 37", "line 38", ..., "line 47"]
    }
    """

    trace = []
    # Also populate a plain-text style backtrace, with github links to frames.
    raw_trace = ["Extracted stack trace:", "{noformat}"]

    extracting_regex = re.compile("([a-zA-Z0-9\./]*)@((?:[a-zA-Z0-9_()]+/?)+\.js):(\d+)(?::\d+)?$")
    n_lines_of_context = 5

    stack_lines = fault.context.splitlines()

    # Sometimes stack traces appear twice: once printed by the assert.js before throwing it, and
    # once by the shell, where all exceptions are printed. Here we ignore the first stack trace and
    # focus on the one printed by the shell.
    if "" in stack_lines:
        stack_lines = stack_lines[stack_lines.index("") + 1:]

    for line in stack_lines:
        line = line.replace("\\", "/")  # Normalize separators.
        stack_match = extracting_regex.search(line)
        if stack_match is None:
            raw_trace.append(line)
            continue

        (func_name, file_path, line_number) = stack_match.groups()
        gui_github_url = gui_github_url_base + file_path + "#L" + line_number
        line_number = int(line_number)
        raw_trace.append("%s@%s:%d" % (func_name, file_path, line_number))

        # add a {code} frame to the comment, showing the line involved in the stack trace, with some
        # context of surrounding lines. Don't do this for the stack frames within assert.js, since
        # they aren't very interesting.
        if file_path.endswith("assert.js"):
            continue

        raw_github_url = raw_github_url_base + file_path
        raw_code = requests.get(raw_github_url).text
        start_line = max(0, line_number - n_lines_of_context)
        end_line = line_number + n_lines_of_context
        code_context = raw_code.splitlines()[start_line:end_line]

        file_name = file_path[file_path.rfind("/") + 1:]
        trace.append({
            "github_url": gui_github_url,
            "first_line_number": start_line,
            "line_number": line_number,
            "file_path": file_path,
            "file_name": file_name,
            "lines": code_context
        })
    raw_trace.append("{noformat}")
    print(trace)

    return raw_trace, trace


def add_fault_comment(jira_api, issue_string, githash, fault):
    """
    TODO
    """
    jira_issue = jira_api.get_bfg_issue(issue_string)

    raw_github_url_base = "https://raw.githubusercontent.com/mongodb/mongo/" + githash + "/"
    gui_github_url_base = "https://github.com/mongodb/mongo/blob/" + githash + "/"

    new_description_lines = [""]
    if fault.category == "js backtrace":
        raw_trace, trace = build_backtraces(fault, gui_github_url_base, raw_github_url_base)
        new_description_lines.extend(raw_trace)
        new_description_lines.append("\n")

        for i in range(len(trace)):
            frame = trace[i]
            frame_title = "Frame {frame_number}: [{path}:{line_number}|{url}]".format(
                frame_number=len(trace) - (i + 1),
                path=frame["file_path"],
                line_number=frame["line_number"],
                url=frame["github_url"]
            )

            # raw text is 0-based, code lines are 1-based.
            first_line = frame["first_line_number"] + 1
            code_block_header = "{code:js|title=%s|linenumbers=true|firstline=%d|highlight=%d}" % (
                frame["file_name"],
                first_line,
                frame["line_number"])

            new_description_lines.append("")
            new_description_lines.append(frame_title)
            new_description_lines.append(code_block_header)
            new_description_lines.extend(frame["lines"])
            new_description_lines.append("{code}")
    else:
        new_description_lines = [
            "Extracted {fault_type}: ".format(fault_type=fault.category),
            "{noformat}",
            fault.context,
            "{noformat}"
        ]

    print("Updating issue description: \n" + "\n".join(new_description_lines))
    try:
        if "bot-analyzed" not in jira_issue.fields.labels:
            jira_issue.update(
                description=jira_issue.fields.description + "\n".join(new_description_lines))
    except jira_client.JIRAError as e:
        print("Error updating JIRA: " + str(e))


def main():
    parser = argparse.ArgumentParser(description="Extracts javascript backtrace from BFG ticket")

    parser.add_argument("ticket_numbers",
                        type=str,
                        nargs='+',
                        help="the BFG ticket number(s) to analyze, specified as either BFG-XXXX or"
                             " simply XXXX")
    args = parser.parse_args()
    for ticket_number in args.ticket_numbers:
        jira_api = get_jira_client()
        jira_issue = jira_api.get_bfg_issue(ticket_number)

        githash = "215c249c"
        add_fault_comment(
            jira_api,
            "BFG-2724",
            githash,
            faultinfo.FaultInfo(
                "Test",
                "js backtrace",
                """[js_test:upgrade_cluster] 2017-02-13T19:30:39.734+0000  [thread1] Error: assert.soon failed, msg:Awaiting secondaries :
[js_test:upgrade_cluster] 2017-02-13T19:30:39.734+0000 doassert@src/mongo/shell/assert.js:18:14
[js_test:upgrade_cluster] 2017-02-13T19:30:39.734+0000 assert.soon@src/mongo/shell/assert.js:202:13
[js_test:upgrade_cluster] 2017-02-13T19:30:39.734+0000 assert.soonNoExcept@src/mongo/shell/assert.js:215:5
[js_test:upgrade_cluster] 2017-02-13T19:30:39.734+0000 ReplSetTest/this.awaitSecondaryNodes@src/mongo/shell/replsettest.js:501:1
[js_test:upgrade_cluster] 2017-02-13T19:30:39.734+0000 ReplSetTest/this.initiate@src/mongo/shell/replsettest.js:761:13
[js_test:upgrade_cluster] 2017-02-13T19:30:39.734+0000 ShardingTest@src/mongo/shell/shardingtest.js:1309:5
[js_test:upgrade_cluster] 2017-02-13T19:30:39.734+0000 runTest@jstests/multiVersion/upgrade_cluster.js:33:18
[js_test:upgrade_cluster] 2017-02-13T19:30:39.735+0000 @jstests/multiVersion/upgrade_cluster.js:112:5
[js_test:upgrade_cluster] 2017-02-13T19:30:39.735+0000 @jstests/multiVersion/upgrade_cluster.js:8:2""",
                0)
        )
        return
        # Fetch the issue from JIRA.

        # Extract the git hash from the issue summary.
        issue_summary_re = re.compile(".*\[MongoDB \(.*\) @ ([a-z0-9A-Z]+)\]")
        githash = issue_summary_re.match(jira_issue.fields.summary).groups()[0]

        for line in jira_issue.fields.description.splitlines():
            # Looks for a line of the form "*testname.js* - [Logs|log_url] | [History|history_url]"
            if not line.startswith("*"):
                continue

            failing_test_re = re.compile("\*[a-z0-9A-Z_]+\.js\* - \[Logs\|(.*)\] | \[History.*")
            test_log_match = failing_test_re.match(line)
            if not test_log_match:
                continue

            failing_test_logs_url = test_log_match.groups()[0]

            # Parse the log file, extracting backtraces and the like.
            raw_logs = requests.get(failing_test_logs_url + "?raw=1").text
            LFS = log_file_analyzer.LogFileSplitter(raw_logs)
            analyzer = log_file_analyzer.LogFileAnalyzer(LFS.getsplits())
            analyzer.analyze()

            for fault in analyzer.get_faults():
                add_fault_comment(jira_api, ticket_number, githash, fault)

if __name__ == "__main__":
    main()
