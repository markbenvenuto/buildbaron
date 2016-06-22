
## To do analysis of build failures

Install

```
pip2 install -r www/requirements.txt
```

It is a two step process:

1. Do the analysis

```
python2 evg_analyzer.py
```

2. View the analysis

```
python2 www\runserver.py
```
The webserver is running at <http://localhost:5555/>

## Other Useful Scripts

*Scripts to deduplicate stacks from hang_analyzer.py*

```
python2 win_deadlock_analyzer.py
python2 gdb_deadlock_analyzer.py
```
