#!/usr/bin/env python3
"""
Analyze a parallel test failure (basic.js or basicPlus.js).
"""
import argparse
import json
import os
import re
import sys

from functools import reduce


if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(os.path.abspath(os.path.realpath(__file__))))
    print(sys.path)
    import faultinfo
else:
    from . import faultinfo


class ParallelTestFailureAnalyzer(object):
    """Analyze a parallel test log file"""

    def __init__(self, logstr):
        self.faults = []
        self.lines = logstr.split("\n")

    def analyze(self):
        """
        Totally ripped off from unfinished_parallel_suite_tests.py from kernel-tools repo
        """
        test_re = re.compile(r'S(?P<job_num>0|1|2|3) Test : (?P<test_name>.*\.js) (?P<endl>.*$)')
        time_re = re.compile(r'(?P<time>\d*)ms')

        unfinished_tests = set()
        test_data = []
        last_job = {
            0: [],
            1: [],
            2: [],
            3: []
        }
        invariants = 0

        for line in self.lines:
            line_lower = line.lower()

            if '<html>' in line_lower:
                print("ERROR: Please pass a link to the plain text file and not"
                      " one that is HTML formatted")
                sys.exit(1)

            if 'invariant' in line_lower:
                invariants += 1

            match = test_re.search(line)
            if match:
                test_name = match.group('test_name')
                job_num = int(match.group('job_num'))
                endl = match.group('endl')

                if endl == '...':
                    unfinished_tests.add(test_name)
                    last_job[job_num].append(test_name)
                else:
                    match = time_re.search(endl)
                    time = float(match.group('time'))/1000.0
                    unfinished_tests.discard(test_name)
                    test_data.append((time, test_name))
                    last_job[job_num].pop()

        if not test_data:
            # Nothing found.
            print("No information extracted from basic.js or basicPlus.js failure")
            return
        else:
            output = '''Invariants:            {invariants}

Unfinished tests:      {unfinished}
By thread:
    S0:                {s0}
    S1:                {s1}
    S2:                {s2}
    S3:                {s3}

Number of tests:       {num_tests}
Total time:            {total} seconds ({total_hr} hours)
Longest test:          {max} seconds ({max_name})
Shortest test:         {min} seconds ({min_name})
Median execution time: {median} seconds'''
            test_data.sort()
            total = reduce(lambda x, y: x+y[0], test_data, 0.0)
            median = test_data[len(test_data) // 2][0]
            self.faults.append(faultinfo.FaultInfo(
                "Log file",
                "basic.js or basicPlus.js analysis",
                output.format(invariants=invariants,
                              s0=last_job[0],
                              s1=last_job[1],
                              s2=last_job[2],
                              s3=last_job[3],
                              unfinished=unfinished_tests,
                              total=total,
                              total_hr=total/3600,
                              num_tests=len(test_data),
                              max=test_data[-1][0],
                              max_name=test_data[-1][1],
                              min=test_data[0][0],
                              min_name=test_data[0][1],
                              median=median),
                None))

    def get_faults(self):
        return self.faults

    def to_json(self):
        d1 = {"faults": self.faults, "contexts": self.contexts}
        return json.dumps(d1, cls=faultinfo.CustomEncoder)


def main():
    parser = argparse.ArgumentParser(description='Process parallel test\'s log file.')

    parser.add_argument("files", type=str, nargs='+', help="the file to read")
    args = parser.parse_args()

    for file in args.files:

        with open(file, "rb") as lfh:
            log_file_str = lfh.read().decode('utf-8')

        analyzer = ParallelTestFailureAnalyzer(log_file_str)

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


if __name__ == '__main__':
    main()
