
## Requirements

Requires Python 3.5+.

## To do analysis of build failures

Install python modules.

```
pip3 install -r requirements.txt
```

Ensure `evergreen.yml` is setup. For Jira identity, create a file `$HOME/.buildbaron.yaml` with
```
user: <JIRA_USER_NAME>
```
so the tool knows which jira account to user. You will be prompted for your password the first
time it is run, and the password will be stored in the system key ring.

Analysis is a two step process. An offline analysis, and a separate viewer script.

1. Do the analysis

Running the analyzer requires that you have a mongod running on localhost on port 27017.

```
python3 bfg_analyzer.py
```

This script will query the BFG project in Jira. It will use the ticket description to get a list
of failing tests and tasks. It will then download the approriate task logs, system logs, and test
logs to try to determine why the it failed. It runs the logs through various log file analyzers:
* `analyzer/log_file_analyzer.py` - analyzes a test log and looks for asserts, invariants, and Other
  messages
* `analyzer/evg_log_file_analyzer.py` - analyzes an evergreen task or system log - used for system
  failuresand for OOM killer searches.
* `analyzer/timeout_file_analyzer.py` - analyzes an evergreen task log for a list of tests that
  were still running when the test finished.

It caches all files and already completed analysis in `cache\bf`. 
 See [Implementation](#implementation) below.

2. View the analysis

Running the server requires that you have a mongod running on localhost on port 27017.

```
python3 www\runserver.py
```
The webserver is running at <http://localhost:5555/>

## Command Line Options

Here are the available options. By default it will query this week's build baron queue.
```
usage: bfg_analyzer.py [-h] [--jira_server JIRA_SERVER]
                       [--jira_user JIRA_USER]
                       [--last_week | --this_week | --query_str QUERY_STR]

Analyze test failure in jira.

optional arguments:
  -h, --help            show this help message and exit
  --last_week           Query of Last week's build baron queue
  --this_week           Query of This week's build baron queue
  --query_str QUERY_STR
                        Any query against implicitly the BFG project

Jira options:
  --jira_server JIRA_SERVER
                        Jira Server to query
  --jira_user JIRA_USER
                        Jira user name
```

## Implementation

`bfg_analyzer.py` queries jira, and stores the results of its analysis in `failed_bfs.json`.
The list of candidate issues is stored in `bfs.json`. It has simply caching so that it will not
reanalyze tests it has already checked. If you need to redo analysis, delete `summary.json` files.

All log files are cached in `cache\bf\<HASH>\' for a given task. The `bfg_analyzer.py` stores
the following files
* `cache\bf\<TASK_HASH>\test.log` - evergreen task raw log
* `cache\bf\<TASK_HASH>\system.log` - evergreen task system raw log
* `cache\bf\<TASK_HASH>\<TEST_HASH>\test.log` - logkeeper test raw log
* `cache\bf\<TASK_HASH>\<TEST_HASH>\summary.json` - summary of test analysis

## Other Useful Scripts

Scripts to deduplicate stacks from hang_analyzer.py

```
python3 win_deadlock_analyzer.py
python3 gdb_deadlock_analyzer.py
```
