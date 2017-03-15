"""
Routes and views for the flask application.
"""
import json

from datetime import datetime
from flask import request
from flask import render_template, g
from www import app
import os
import pymongo
from pymongo import MongoClient
import sys
import threading

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(os.path.realpath(__file__))))))
print(sys.path)

lib_path = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(os.path.realpath(__file__)))))
print(lib_path)

import analyzer.jira_client
import analyzer.analyzer_config


@app.route('/')
@app.route('/home')
def home():
    """Renders the home page."""

    with open(os.path.join(lib_path, "failed_bfs.json"), "rb") as sjh:
        contents = sjh.read().decode('utf-8')
        failed_bfs_root = json.loads(contents)

    query = failed_bfs_root['query']
    date = failed_bfs_root['date']
    failed_bfs = failed_bfs_root['bfs']

    # TODO this is a mess.  Load from collection.
    client = MongoClient('localhost', 27017)
    coll = client['buildbaron']['open_bfgs']
    bfs = list(coll.find())

    return render_template(
        'index.html',
        title='Home Page',
        year=datetime.now().year,
        failed_bfs=bfs,
        query=query,
        date=date,
        bf_count=len(bfs))


statusKeys = {
    "Blocked": 1,
    "Open": 1,
    "In Progress": 1,
    'Waiting for bug fix': 1,
    'Waiting For User Input': 1,
    'In Code Review': 1,
    "Needs Scope": 1,
    "Requested": 1,
    'Stuck': 1,
    "Closed": 2,
    "Resolved": 2,
    "Completed": 2,
}


def issue_sort(issue):

    k1 = statusKeys[issue.fields.status.name]

    if (k1 == 2):
        if issue.fields.resolution is not None and issue.fields.resolution.name == "Fixed":
            k2 = 0
        else:
            k2 = 1
    else:
        k2 = 3

    return "%d_%d" % (k1, k2)


jira_client_cached = None
jira_client_lock = threading.Lock()


def get_jira_client():
    global jira_client_cached
    global jira_client_lock

    with jira_client_lock:
        if jira_client_cached is None:
            print("Connecting to JIRA.....")
            jira_client_cached = analyzer.jira_client.jira_client(
                analyzer.analyzer_config.jira_server(), analyzer.analyzer_config.jira_user())
        return jira_client_cached


@app.route('/failure')
def failure():
    """Renders the failure page."""
    with open(os.path.join(lib_path, "failed_bfs.json"), "rb") as sjh:
        contents = sjh.read().decode('utf-8')
        failed_bfs_root = json.loads(contents)

    failed_bfs = failed_bfs_root['bfs']

    issue = request.args.get('issue')
    test_name = request.args.get('test_name')

    failed_bf = None

    for bfg in failed_bfs:
        if bfg["bfg_info"]["issue"] == issue:
            failed_bf = bfg

    assert (failed_bf is not None), ("could not find matching test. "
                                     "Looking for issue '{0}' within {1}".format(
                                         issue,
                                         [bf["bfg_info"]["issue"] for bf in failed_bfs]))

    # Query Predicates
    def remove_special_characters(string):
        """Filter special characters out of search terms for Jira."""
        new_string = ""
        for c in string:
            if c not in ["]", "}", "[", "{", "(", ")", "\\", '"', "'"]:
                new_string += c
        return new_string

    def flatten(a):
        flattened = []
        for elem in a:
            if type(elem) == list:
                flattened.extend(elem)
            else:
                flattened.append(elem)
        return flattened

    jira_text_terms = [os.path.basename(test_name), failed_bf['bfg_info']['suite']]
    all_faults = (
        failed_bf["faults"] +
        flatten([testinfo["faults"] for testinfo in failed_bf["test_faults"]])
    )

    jira_text_terms.extend(
        [remove_special_characters(fault["context"].splitlines()[0]) for fault in all_faults])
    jc = get_jira_client()
    jira_query = jc.query_duplicates_text(jira_text_terms)
    issues = jc.search_issues(jira_query)

    issues.sort(key=issue_sort)

    is_system_failure = "System Failure" in failed_bf['bfg_info']['summary']

    # Query for the last few issues the user has looked at
    recent_issues_query = ("issuekey in issueHistory()"
                           " and project in (bf, server, evg, build)"
                           " ORDER BY lastViewed DESC")
    recent_issues = jc.search_issues(recent_issues_query)

    recent_issues.sort(key=issue_sort)

    return render_template(
        'failure.html',
        title='Failure Details',
        year=datetime.now().year,
        failed_bf=failed_bf,
        issues=issues,
        jira_query=jira_query,
        recent_issues=recent_issues,
        recent_issues_query=recent_issues_query,
        is_system_failure=is_system_failure)


@app.route('/bfg_text_search', methods=['POST'])
def bfg_text_search():
    """Filter issues by the given text."""
    text = request.json["text"]

    # Get the matching tickets from the database
    client = MongoClient('localhost', 27017)
    coll = client['buildbaron']['open_bfgs']

    res = coll.find({ '$text': { '$search': text }})

    query = "{ '$text' : { '$search' :' " + text + "' }}"
    date = "??"

    # todo: make this work better with cursors...
    filtered_bfs = list(res)

    # Re-load the home page with the filtered tickets
    return render_template(
        'index.html',
        title='Home Page',
        year=datetime.now().year,
        failed_bfs=filtered_bfs,
        query=query,
        date=date,
        bf_count=len(filtered_bfs))


@app.route('/close_duplicate_home_page', methods=['POST'])
def close_duplicate_home_page():
    """Closes a duplicate ticket from the home page."""

    issue = request.json["issue"]
    duplicate = request.json["duplicate"]

    print("got request to close " + issue + " as a dup of " + duplicate)
    try:
        jc = get_jira_client()
        jc.close_as_duplicate(issue, duplicate)

        return "ok"

    except analyzer.jira_client.JIRAError as err:
        print(err)
        message = "Couldn't close ticket: " + err.text

        return message

    return "ok"


@app.route('/close_duplicate')
def close_duplicate():
    """Renders the duplicate page."""

    issue = request.args.get('issue')
    duplicate_issue = request.args.get('duplicate_issue')

    jc = get_jira_client()

    jc.close_as_duplicate(issue, duplicate_issue)

    return render_template(
        'duplicate.html',
        title='Ticket Closed',
        year=datetime.now().year,
        issue=issue,
        duplicate_issue=duplicate_issue)


@app.route('/close_goneaway')
def close_goneaway():
    """Renders the gone away page."""

    issue = request.args.get('issue')

    jc = get_jira_client()
    jc.close_as_goneaway(issue)

    return render_template(
        'gone_away.html', title='Ticket Resolved', year=datetime.now().year, issue=issue)


@app.route('/bulk_close_duplicate', methods=['POST'])
def bulk_close_duplicate():
    """Renders the bulk duplicate page."""

    user_errors = []
    ticket_successes = []
    ticket_failures = []
    issues = request.form.getlist('issues')
    duplicated_ticket = request.form.get('duplicated_ticket')

    if not issues:
        user_errors.append("You didn't select any issues to close.")
    elif not duplicated_ticket:
        user_errors.append("You must specify which ticket is the duplicated issue.")
    else:
        jc = get_jira_client()
        for issue in issues:
            try:
                jc.close_as_duplicate(issue, duplicated_ticket)
                ticket_successes.append(issue)
            except analyzer.jira_client.JIRAError as err:
                print(err)
                error_info = {
                    "ticket": issue,
                    "reason": err.text,
                    "url": err.url
                }
                ticket_failures.append(error_info)

    return render_template(
            'bulk_duplicate.html',
            title='Tickets Closed',
            jira_server=analyzer.analyzer_config.jira_server(),
            year=datetime.now().year,
            duplicate_issue=duplicated_ticket,
            ticket_successes=ticket_successes,
            ticket_failures=ticket_failures,
            user_errors=user_errors)


@app.route('/about')
def about():
    """Renders the about page."""
    return render_template(
        'about.html',
        title='About',
        year=datetime.now().year,
        message='Third Party License Notices')
