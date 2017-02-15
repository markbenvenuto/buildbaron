"""
Jira Client and utility operations
"""
import getpass
import re
import threading

from jira import JIRA, JIRAError

try:
    import keyring
except ImportError:
    keyring = None


class jira_client(object):
    """Simple wrapper around jira api for build baron analyzer needs"""

    def __init__(self, jira_server, jira_user):

        self.jira = JIRA(
            options={'server': jira_server,
                     'verify': False},
            basic_auth=(jira_user, jira_client._get_password(jira_server, jira_user)),
            validate=True)

        # Since the web server may share this client among threads, use a lock since it unclear if
        # the JIRA client is thread-safe
        self._lock = threading.Lock()

    @staticmethod
    def _get_password(server, user):
        global keyring

        password = None

        if keyring:
            try:
                password = keyring.get_password(server, user)
            except:
                print("Failed to get password from keyring")
                keyring = None

        if password is not None:
            print("Using password from system keyring.")
        else:
            password = getpass.getpass("Jira Password:")

            if keyring:
                answer = raw_input("Store password in system keyring? (y/N): ").strip()

                if answer == "y":
                    keyring.set_password(server, user, password)

        return password

    def query_duplicates_text(self, fields):
        search = "project in (bf, server, evg, build) AND (" + " or ".join(
            ['text~"%s"' % f for f in fields]) + ")"
        return search

    def search_issues(self, query, maxResults=50):
        with self._lock:
            results = self.jira.search_issues(
                query,
                fields=[
                    "id", "key", "status", "resolution", "summary", "created", "updated",
                    "assignee", "description"
                ],
                maxResults=maxResults)

        print("Found %d results" % len(results))

        return results

    def add_affected_version(self, bf_issue, affected_version_string):
        """
        Adds a new 'affectedVersion' to a BF, or does nothing if the version is already present in
        the issue's 'affectedVersions'.
        """
        affected_versions = bf_issue.fields.versions
        if affected_versions is None:
            affected_versions = []
        else:
            affected_versions = [{"name": v.name} for v in affected_versions]
        if affected_version_string.lower() == "master":
            affected_version_string = "3.6"

        if {"name": affected_version_string} in affected_versions:
            return

        affected_versions.append({"name": affected_version_string})
        try:
            bf_issue.update(fields={"versions": affected_versions})
        except JIRAError as e:
            print("Error updating issue's affected versions: " + str(e))

    def add_failing_task(self, bf_issue, failing_task_string):
        """
        Adds a new 'failing_task' to a BF, or does nothing if the version is already present in
        the issue's 'failing_task's.
        """
        failing_tasks = bf_issue.fields.customfield_12950
        if failing_tasks is None:
            failing_tasks = []

        if failing_task_string in failing_tasks:
            return
        failing_tasks.append(failing_task_string)

        try:
            bf_issue.update(fields={"customfield_12950": failing_tasks})
        except JIRAError as e:
            print("Error updating duplicate issue's failing_tasks: " + str(e))

    def add_affected_variant(self, bf_issue, variant_string):
        """
        Adds a new 'Buildvariant' to a BF, or does nothing if the version is already present in
        the issue's 'Buildvariant's, or if 'variant_string' is not a valid Buildvariant specifier.
        The latter case can happen if it is the name of a variant on an old branch, or if it is a
        new variant and we haven't updated JIRA yet.
        """
        affected_variants = bf_issue.fields.customfield_11454
        if affected_variants is None:
            affected_variants = []
        else:
            affected_variants = [{"value": v.value} for v in affected_variants]

        if {"value": variant_string} in affected_variants:
            return

        affected_variants.append({"value": variant_string})

        try:
            bf_issue.update(fields={"customfield_11454": affected_variants})
        except JIRAError as e:
            print("Error updating duplicate issue's Buildvariants: " + str(e))

    def add_github_backtrace_context(self, issue_string, backtrace):
        """
        Given a backtrace represented as follows, appends some nicely formatted previews of the code
        involved in the backtrace and adds them to the issue's description.

        A backtrace is a list of frames, specified as follows:
        {
          "github_url": "https://github.com/mongodb/mongo/blob/deadbeef/jstests/core/test.js#L42",
          "first_line_number": 37,
          "line_number": 42,
          "file": "jstests/core/test.js",
          "file_name": "test.js",
          "lines": ["line 37", "line 38", ..., "line 47"]
        }
        """
        new_description_lines = [""]
        for i in range(len(backtrace)):
            frame = backtrace[i]
            frame_title = "Frame {frame_number}: [{path}:{line_number}|{url}]".format(
                frame_number=len(backtrace) - (i + 1),
                path=frame["file_path"],
                line_number=frame["line_number"],
                url=frame["github_url"]
            )

            # raw text is 0-based, code lines are 1-based.
            first_line = frame["first_line_number"] + 1
            code_block_header = (
                "{code:js|title=%s|linenumbers=true|firstline=%d|highlight=%d}" %
                (frame["file_name"], first_line, frame["line_number"])
            )

            new_description_lines.append("")
            new_description_lines.append(frame_title)
            new_description_lines.append(code_block_header)
            new_description_lines.extend(frame["lines"])
            new_description_lines.append("{code}")

        self.append_to_issue_description(issue_string, new_description_lines)

    def add_fault_comment(self, issue_string, fault):
        """
        Adds a {noformat} block to the jira ticket's description summarizing the fault that we have
        extracted from the logs.
        """
        self.append_to_issue_description(
            issue_string,
            [
                "",
                "Extracted {fault_type}: ".format(fault_type=fault.category),
                "{noformat}",
                fault.context,
                "{noformat}"
            ]
        )

    def append_to_issue_description(self, issue_string, new_lines):
        """
        Adds the given text to the bottom of the given issue's description.

        If the ticket has already been tagged with the 'bot-analyzed' tag, no update occurs.
        """
        jira_issue = self.get_bfg_issue(issue_string)
        try:
            if "bot-analyzed" not in jira_issue.fields.labels:
                print("Updating issue description: \n" + "\n".join(new_lines))
                jira_issue.update(
                    description=jira_issue.fields.description + "\n".join(new_lines))
            else:
                print("Skipping issue update, since issue is already tagged with 'bot-analyzed'")
        except jira_client.JIRAError as e:
            print("Error updating JIRA: " + str(e))


# jira.resolutions()
# <JIRA Resolution: name='Fixed', id='1'>
# <JIRA Resolution: name="Won't Fix", id='2'>
# <JIRA Resolution: name='Duplicate', id='3'>
# <JIRA Resolution: name='Incomplete', id='4'>
# <JIRA Resolution: name='Cannot Reproduce', id='5'>
# <JIRA Resolution: name='Works as Designed', id='6'>
# <JIRA Resolution: name='Gone away', id='7'>
# <JIRA Resolution: name='Community Answered', id='8'>
# <JIRA Resolution: name='Done', id='9'>

# Issue link Types
# <JIRA IssueLinkType: name='Backports', id='10420'>
# <JIRA IssueLinkType: name='Depends', id='10011'>
# <JIRA IssueLinkType: name='Documented', id='10320'>
# <JIRA IssueLinkType: name='Duplicate', id='10010'>
# <JIRA IssueLinkType: name='Gantt Dependency', id='10020'>
# <JIRA IssueLinkType: name='Gantt End to End', id='10423'>
# <JIRA IssueLinkType: name='Gantt End to Start', id='10421'>
# <JIRA IssueLinkType: name='Gantt Start to End', id='10424'>
# <JIRA IssueLinkType: name='Gantt Start to Start', id='10422'>
# <JIRA IssueLinkType: name='Related', id='10012'>
# <JIRA IssueLinkType: name='Tested', id='10220'>

    def close_as_duplicate(self, issue, duplicate_issue):

        with self._lock:
            src_issue = self.jira.issue(issue)
            dest_issue = self.jira.issue(duplicate_issue)

            # Add duplicate link
            self.jira.create_issue_link(
                type='Duplicate', inwardIssue=issue, outwardIssue=duplicate_issue)

            # Update affectsVersions, Buildvariants, etc.
            title_parsing_regex = re.compile("(Timed Out|Failures?):"
                                             " (?P<suite_name>.*?)"
                                             " on"
                                             " (?P<variant_prefix>[^\(\[]+)"
                                             "(?P<variant_suffix>"
                                             " (?:"
                                             "\(Clang 3\.7/libc\+\+\)|"
                                             "\(No Journal\)|"
                                             "\(inMemory\)|"
                                             "\(ephemeralForTest\)|"
                                             "\(Unoptimized\))(?: DEBUG)?)?"
                                             " (?:\("
                                             "(?P<test_names>(.*?(\.js|CheckReplDBHash)(, )?)*)"
                                             "\))? ?\[MongoDB \("
                                             "(?P<version>.*?)"
                                             "\) @ [0-9A-Za-z]+\]")

            parsed_title = title_parsing_regex.match(src_issue.fields.summary)
            assert(parsed_title is not None)

            # Update the failing variants.
            variant = parsed_title.group("variant_prefix").rstrip()
            if parsed_title.group("variant_suffix") is not None:
                variant += parsed_title.group("variant_suffix").rstrip()

            self.add_affected_variant(dest_issue, variant)

            # Update the failing tasks.
            self.add_failing_task(dest_issue, parsed_title.group("suite_name"))

            # Update the affected versions.
            self.add_affected_version(dest_issue, parsed_title.group("version"))

            # Close - id 2
            # Duplicate issue is 3
            self.jira.transition_issue(src_issue, '2', resolution={'id': '3'})

    def close_as_goneaway(self, issue):
        with self._lock:
            src_issue = self.jira.issue(issue)

            # Close - id 2
            # Gone away is 7
            self.jira.transition_issue(
                src_issue, '2', comment="Transient machine issue.", resolution={'id': '7'})

    def get_bfg_issue(self, issue_number):
        if not issue_number.startswith("BFG-") and not issue_number.startswith("BF-"):
            issue_number = "BFG-" + issue_number
        with self._lock:
            src_issue = self.jira.issue(issue_number)
        return src_issue
