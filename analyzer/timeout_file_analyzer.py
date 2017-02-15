#!/usr/bin/env python3
"""
Utilities for analyzing an evergreen task log file for timeouts.
"""
import argparse
import json
import os
import re
import sys

if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(os.path.abspath(os.path.realpath(__file__))))
    print(sys.path)
    import faultinfo
else:
    from . import faultinfo

DEFAULT_CONTEXT_LINES = 20


class TimeOutAnalyzer(object):
    """Analyze a log file for a list of incomplete tests"""

    def __init__(self, logstr):
        self.logstr = logstr
        self.faults = []
        self.contexts = []

        self.lines = self.logstr.split("\n")

        self.incomplete_tests = []

    def analyze(self):
        startedTests = []
        completedTests = []
        testLogs = {}

        for idx in range(len(self.lines)):
            line = self.lines[idx]

            if "Task timed out" in line:
                self.add_fault('task time out', idx, 3, 1)

            # JS Test Name
            m = re.search("Running (.*\.js)", line)
            if m:
                startedTests.append(m.group(1))
            else:
                # Unit Test
                m = re.search("Running (.*)\.\.\.", line)
                if m:
                    startedTests.append(m.group(1))

            m = re.search("0000 (.*) ran in", line)
            if m:
                completedTests.append(m.group(1))

            # JS Test Filter
            m = re.search("Writing output of JSTest (.*) to (http.*/)\.", line)
            if m:
                test_name = os.path.basename(m.group(1).replace("\\", "/"))
                testLogs[test_name] = m.group(2)

            # Unit Test Filter
            m = re.search("Writing output of Program (.*) to (http.*/)\.", line)
            if m:
                test_name = os.path.basename(m.group(1))
                testLogs[test_name] = m.group(2)

            m = re.search("Starting Hook (\w+:\w+) under executor \w+\.\.\.", line)
            if m:
                startedTests.append(m.group(1))

            m = re.search("Writing output of Hook (.+:.+) to (http.*/)\.", line)
            if m:
                testLogs[m.group(1)] = m.group(2)

            m = re.search("Hook (.+:+) finished\.", line)
            if m:
                completedTests.append(m.group(1))

        incompleteTestsContext = {}

        for t in list(set(startedTests) - set(completedTests)):
            incompleteTestsContext[t] = testLogs.get(t, '(log url not available)')

        if len(incompleteTestsContext) > 0:
            for test, incomplete in incompleteTestsContext.items():
                self.incomplete_tests.append({'name': test, 'log_file': incomplete})

    def add_fault(self, category, line, before_line_count, after_line_count):
        context = '\n'.join(self.lines[line - before_line_count:line + after_line_count])
        self.faults.append(faultinfo.FaultInfo("evergreen", category, context, line))

    def get_incomplete_tests(self):
        return self.incomplete_tests

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

        analyzer = TimeOutAnalyzer(log_file_str)

        analyzer.analyze()

        if len(analyzer.get_incomplete_tests()) == 0:
            print("===========================")
            print("Analysis failed for test: " + file)
            print("===========================")
            return

        for t in analyzer.get_incomplete_tests():
            print(t)

        print(analyzer.to_json())

        f = json.loads(analyzer.to_json(), cls=faultinfo.CustomDecoder)


if __name__ == '__main__':
    main()
