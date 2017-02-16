#!/usr/bin/env python3
"""
Analyze a evergreen task log page
"""
import argparse
import json
import os
import sys

if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(os.path.abspath(os.path.realpath(__file__))))
    print(sys.path)
    import faultinfo
else:
    from . import faultinfo

DEFAULT_CONTEXT_LINES = 10


class EvgLogFileAnalyzer(object):
    """Analyze a non-timeout evergreen task log file"""

    def __init__(self, logstr):
        self.logstr = logstr
        self.faults = []
        self.contexts = []

        self.lines = self.logstr.split("\n")

    def analyze(self):

        for idx in range(len(self.lines)):
            line = self.lines[idx]

            # All test failures contain this:
            # ("Task completed - FAILURE.")

            # All tests that fail usually have this messsage so
            # there is nothing gained by check for it a normal case when a test simply fails
            if "Task completed - FAILURE" in line:
                self.add_fault('task failure', idx, DEFAULT_CONTEXT_LINES, 0)

            # -1073741819 = 0xC0000005 = Access Violation on Windows
            if "(-1073741819)" in line:
                self.add_fault('test crashed', idx, DEFAULT_CONTEXT_LINES, 5)

            if "fatal: Could not read from remote repository." in line:
                self.add_fault('git hub down', idx, DEFAULT_CONTEXT_LINES, 5)

            self.check_oom(line, idx)

    def check_oom(self, line, idx):
        if "OOM (Out of memory) killed processes detected" in line and not "No OOM (Out of memory) killed processes detected" in line:
            count = 1
            for fidx in range(idx + 1, len(self.lines)):
                if not ("oom-killer" in line or "Out of memory" in line or "Kill process" in line):
                    break
                count += 1

            context = '\n'.join(self.lines[idx:idx + count])
            self.faults.append(faultinfo.FaultInfo("evergreen", "oom-killer", context, line))

    def analyze_oom(self):
        for idx in range(len(self.lines)):
            line = self.lines[idx]

            self.check_oom(line, idx)

    def add_fault(self, category, line, before_line_count, after_line_count):
        context = '\n'.join(self.lines[line - before_line_count:line + after_line_count])
        self.faults.append(faultinfo.FaultInfo("evergreen", category, context, line))

    def get_faults(self):
        return self.faults

    def get_contexts(self):
        return self.contexts

    def to_json(self):
        d1 = {"faults": self.faults, "contexts": self.contexts}
        return json.dumps(d1, cls=faultinfo.CustomEncoder)


def main():
    parser = argparse.ArgumentParser(description='Process log file.')

    parser.add_argument("files", type=str, nargs='+', help="the file to read")
    args = parser.parse_args()

    for file in args.files:

        with open(file, "rb") as lfh:
            log_file_str = lfh.read().decode('utf-8')

        analyzer = EvgLogFileAnalyzer(log_file_str)

        analyzer.analyze()

        faults = analyzer.get_faults()

        if len(faults) == 0:
            print("===========================")
            print("Analysis failed for test: " + file)
            print("===========================")
            return

        for f in analyzer.get_faults():
            print(f)

        print(analyzer.to_json())

        f = json.loads(analyzer.to_json(), cls=faultinfo.CustomDecoder)
        #print (f);


if __name__ == '__main__':
    main()
