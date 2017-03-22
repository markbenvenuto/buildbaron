"""
Module to control access to bfs data stored in MongoDB
"""
import pymongo
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

DB_NAME = 'buildbaron'
COLL_NAME = 'open_bfgs'

# TODO: don't connect every time, this is dumb.
def get_client():
    client = MongoClient('localhost', 27017)

    try:
        # The ismaster command is cheap and does not require auth.
        client.admin.command('ismaster')
    except ConnectionFailure:
        print("Server not available")

    return client


def get_bf_collection():
    client = get_client()
    coll = client[DB_NAME][COLL_NAME]
    return coll


def reinit_db():
    client = get_client()
    db = client[DB_NAME]
    coll = db[COLL_NAME]

    # Start with a clean state
    coll.drop()

    coll = db.create_collection(COLL_NAME)

    # Create a text index on everything for search later
    coll.create_index([("$**", pymongo.TEXT)])

def load_bfs(failed_bfs):
    coll = get_bf_collection()

    for bf in failed_bfs:
        coll.insert_one(bf)
        print("Inserted " + bf['bfg_info']['issue'])


def get_bfs():
    coll = get_bf_collection()
    bfs = list(coll.find())
    return bfs


def remove_issue(issue): 
    coll = get_bf_collection()
    # Remove from our collection
    coll.remove({ 'bfg_info.issue' : issue })


def get_bfs_via_text(text):
    coll = get_bf_collection()

    res = coll.find({ '$text': { '$search': text }})

    # todo: make this work better with cursors...
    filtered_bfs = list(res)
