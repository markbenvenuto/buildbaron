#!/usr/bin/env3 python
"""
A JS Test/Unit test log file analyzer
"""
import argparse
import os
import re
import sys

if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(os.path.abspath(os.path.realpath(__file__))))
    import faultinfo
else:
    from . import faultinfo

ROOT = "Root"
TEST = "Test"


class LineInfo:
    def __init__(self, line_number, line):
        self.line_number = line_number
        self.line = line

        self.start = 0

    def get_line(self):
        return self.line

    def get_line_number(self):
        return self.line_number

    def get_start(self):
        return self.start

    def set_start(self, val):
        self.start = val

    def __str__(self):
        return self.line


class LogFileSplitter:
    """
    The log file for an individual test logically contains many different logs all intermingled into
    one. There are usually at least two different categories of logs: logs from the test itself (
    which might include things like js assertion failures), and logs from the processes running
    (e.g. the processes started by a ShardingTest, or the fixture started by resmoke).

    This class will split each set of logs into separate 'streams', so regexes attempting to look
    for common log patterns can be applied one logical stream of logs without risking an unrelated
    log line from another process appearing between lines of another stream.
    """

    GLOBAL_STREAM = 0
    PROCESS_STREAM = 1
    TEST_STREAM = 2

    def __init__(self, lstr):
        re_test_prefix = re.compile(
            '^\[(js_test|cpp_unit_test|db_test|cpp_integration_test|CheckReplDBHash):(.*?)\] ')
        re_fixture_prefix = re.compile('^\[(.*?:job[0-9]+(?::[^\]]*?)?)\] ')
        re_time = re.compile(
            '^(-?(?:[1-9][0-9]*)?[0-9]{4})-'
            '(1[0-2]|0[1-9])-'
            '(3[01]|0[1-9]|[12][0-9])T(2[0-3]|[01][0-9]):'
            '([0-5][0-9]):'
            '([0-5][0-9])'
            '(\.[0-9]+)?'
            '(Z|[+-][0-9]{4})? '
        )
        re_server = re.compile('^(([cds]|sh)[0-9]{5})\|')

        lines = lstr.splitlines()
        self.streams = {}
        line_number = 1
        for line in lines:
            test_prefix_match = re_test_prefix.match(line)
            entire_line = LineInfo(line_number, line)

            if test_prefix_match:
                test_name = test_prefix_match.groups()[1]
                remaining = line[test_prefix_match.end():]
                time_match = re_time.match(remaining)
                assert(time_match)
                remaining = remaining[time_match.end(0):]
                server_match = re_server.match(remaining)
                if server_match:
                    # These are the logs of an individual process, e.g. mongod or mongos.
                    process_name = server_match.groups()[0]
                    self.add_line_to_stream(process_name,
                                            LogFileSplitter.PROCESS_STREAM,
                                            LineInfo(line_number, remaining))
                else:
                    self.add_line_to_stream(test_name,
                                            LogFileSplitter.TEST_STREAM,
                                            LineInfo(line_number, remaining))
            else:
                fixture_match = re_fixture_prefix.match(line)
                if fixture_match:
                    remaining = line[fixture_match.end():]
                    self.add_line_to_stream(fixture_match.groups()[0],
                                            LogFileSplitter.PROCESS_STREAM,
                                            LineInfo(line_number, remaining))
                else:
                    self.add_line_to_stream(ROOT, LogFileSplitter.GLOBAL_STREAM, entire_line)
            line_number += 1

    def add_line_to_stream(self, stream_id, stream_type, line_info):
        if stream_id not in self.streams:
            self.streams[stream_id] = {"stream_type": stream_type, "id": stream_id, "lines": []}
        self.streams[stream_id]["lines"].append(line_info)

    def get_streams(self):
        return self.streams


class LogFileAnalyzer:
    """
    TODO(CWS)
    """

    PROCESS_FAULTS = [
        {
            "fault_type": "C runtime error",
            "regex": re.compile(r"\*\*\* C runtime error.*$"),
        },
        {
            "fault_type": "fatal assertion",
            "begin_regex": re.compile(r"Fatal assertion.*?$"),
            "multi_line": True,
            "end_regex": re.compile(r"\*\*\*aborting after fassert failure"),
        },
        {
            "fault_type": "invariant failure",
            "begin_regex": re.compile(r"Invariant failure.*?$"),
            "multi_line": True,
            "end_regex": re.compile(r"\*\*\*aborting after invariant\(\) failure"),
        },
        {
            "fault_type": "Segmentation fault",
            "begin_regex": re.compile(r"Invariant failure.*?$"),
            "multi_line": True,
            "end_regex": re.compile(r"END BACKTRACE"),
        },
        {
            "fault_type": "mongos startup failure",
            "regex": re.compile(r"Error: Failed to start mongos.*?failed.*?js"),
        },
        {
            "fault_type": "memory leak(s)",
            "begin_regex": re.compile(r"LeakSanitizer: detected memory leaks.*?$"),
            "multi_line": True,
            "end_regex": re.compile(r"SUMMARY: AddressSanitizer.*?$"),
        },
        {
            "fault_type": "tcmalloc memory corruption",
            "begin_regex": re.compile(r"Found a corrupted memory buffer in MallocBlock.*?$"),
            "multi_line": True,
            "end_regex": re.compile(r"END BACKTRACE"),
        },
        {
            "fault_type": "teardown failure",
            "regex": re.compile(r"mongo.*?teardown.*?wasn't\.$"),
        },
    ]

    TEST_FAULTS = [
        {
            "fault_type": "parallel test failure",
            "regex": re.compile(r"Parallel Test FAILED: .*$"),
        },
        {
            "fault_type": "js backtrace",
            "begin_regex": re.compile(r"(Error: |assert.*failed)"),
            "multi_line": True,
            "middle_regex": re.compile(r"(failed to load:|@[a-zA-Z0-9_/\\<> ]+(\.js|eval):[0-9]+(:[0-9]+)?$)"),
            "end_regex": re.compile(r"failed to load:"),
        },
        {
            "fault_type": "js backtrace",  # this time with a multi-line error.
            "begin_regex": re.compile("\[thread1\] (\w*Error: |assert.*failed)"),
            "multi_line": True,
            "end_regex": re.compile("failed to load"),
        },
        {
            "fault_type": "concurrency failure",
            "begin_regex": re.compile("Error: \d+ threads? threw"),
            "multi_line": True,
            "end_regex": re.compile("failed to load:"),
        },
        {
            "fault_type": "unittest failure",
            "regex": re.compile(r"FAIL: "),
        },
        {
            "fault_type": "bad exit code",
            "regex": re.compile(r"StopError:.*?exited with error code -?(\d+)$"),
        },
        {
            "fault_type": "fatal assertion",
            "begin_regex": re.compile(r"Fatal assertion.*?$"),
            "multi_line": True,
            "end_regex": re.compile(r"\*\*\*aborting after fassert failure"),
        },
        {
            "fault_type": "invariant failure",
            "begin_regex": re.compile(r"Invariant failure.*?$"),
            "multi_line": True,
            "end_regex": re.compile(r"END BACKTRACE"),
        },
        {
            "fault_type": "Segmentation fault",
            "begin_regex": re.compile(r"Invariant failure.*?$"),
            "multi_line": True,
            "end_regex": re.compile(r"END BACKTRACE"),
        },
        {
            "fault_type": "dbhash mismatch",
            "begin_regex": re.compile("checkReplicatedDataHashes.*different hash for the collection"),
            "multi_line": True,
            "end_regex": re.compile("Dumping collection"),
        },
    ]

    def __init__(self, streams):
        self.streams = streams
        self.joins = {}
        self.faults = []

    def add_fault(self, key, line_number, category, context):
        self.faults.append(faultinfo.FaultInfo(key, category, context, line_number))

    def extract_multiline_regex(self, lines, start_line, end_regex, middle_regex):
        """
        Finds the lines matching a multi-line regex. Will return up to 50 lines after 'start_line',
        adding any line that follows until a line matching 'end_regex' is found.

        If 'middle_regex' is not None, each line in the middle must match this regex. This method
        returns (None, None) if a subsequent line does not match 'middle_regex'.

        Otherwise, returns the line number reached via this method, and the lines between
        'start_line' and the ending line.
        """
        MAX_LINES_PER_FAULT = 50
        line_number = start_line + 1

        while (line_number < len(lines)):
            line = lines[line_number].get_line()
            if middle_regex is not None and middle_regex.search(line) is None:
                return None, None
            if end_regex.search(line) is not None or line_number - start_line > MAX_LINES_PER_FAULT:
                break
            line_number += 1

        if line_number == start_line:
            line_number = start_line + 1

        return line_number, lines[start_line:line_number]

    def extract_faults_from_stream(self, stream, fault_specs):
        stream_line_num = 0
        while (stream_line_num < len(stream["lines"])):
            for fault in fault_specs:
                line_info = stream["lines"][stream_line_num]
                if fault.get("multi_line", False):
                    # This is a multi-line regex. We check this line for a match with the start
                    # regex, then keep looking until we find a match for the end regex.
                    fault_start_regex = fault["begin_regex"]
                    fault_match = fault_start_regex.search(line_info.get_line())
                    if fault_match is None:
                        continue

                    new_line_number, fault_context = self.extract_multiline_regex(
                        stream["lines"],
                        stream_line_num,
                        fault["end_regex"],
                        fault.get("middle_regex", None))

                    if (new_line_number, fault_context) == (None, None):
                        continue

                    self.add_fault(stream["id"],
                                   fault_context[0].get_line_number(),
                                   fault["fault_type"],
                                   "\n".join([l_info.get_line() for l_info in fault_context]))
                    stream_line_num = new_line_number
                    if stream_line_num >= len(stream["lines"]):
                        break
                else:
                    # Easy case - a single line fault.
                    fault_regex = re.compile(fault["regex"])
                    fault_match = fault_regex.search(line_info.get_line())
                    if fault_match is not None:
                        self.add_fault(stream["id"],
                                       line_info.get_line_number(),
                                       fault["fault_type"],
                                       line_info.get_line())
            stream_line_num += 1

    def analyze(self):
        for stream_id in self.streams:
            stream = self.streams[stream_id]
            if stream["stream_type"] == LogFileSplitter.PROCESS_STREAM:
                self.extract_faults_from_stream(stream, LogFileAnalyzer.PROCESS_FAULTS)
            elif stream["stream_type"] == LogFileSplitter.TEST_STREAM:
                self.extract_faults_from_stream(stream, LogFileAnalyzer.TEST_FAULTS)

    def get_faults(self):
        return self.faults


def main():
    parser = argparse.ArgumentParser(description='Process log file.')

    parser.add_argument("files", type=str, nargs='+', help="the file to read")
    args = parser.parse_args()

    for file in args.files:

        with open(file, "rb") as lfh:
            log_file_str = lfh.read().decode('utf-8')

        LFS = LogFileSplitter(log_file_str)

        analyzer = LogFileAnalyzer(LFS.get_streams())

        analyzer.analyze()

        faults = analyzer.get_faults()

        if len(faults) == 0:
            print("===========================")
            print("No faults found failed for test: " + file)
            print("===========================")
            return

        for f in analyzer.get_faults():
            print(f)

if __name__ == '__main__':
    main()
