"""
Utility classes for reporting information from log file analyzers
"""
import json


# In the future, add tests cases for test result classification
#
class FaultInfo(object):
    """
    Describes a fault or a context from a log file that caused it to fail
    Source - Shell, MongoD, etc
    Category - js assert, seg fault, etc
    Context - lines from file
    Line number - line number in log file
    """

    def __init__(self, source, category, context, line_number):
        self.source = source
        self.category = category
        self.context = context
        self.line_number = line_number

    def __str__(self):
        return "FaultInfo -- {0} - {1}\n{2}".format(self.source,
                                                    self.category,
                                                    self.context)

    def to_json(self):
        return {
            "source": self.source,
            "category": self.category,
            "context": self.context,
            "line_number": self.line_number
        }


class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, FaultInfo):
            return {
                "category": obj.category,
                "context": obj.context,
                "source": obj.source,
                "line_number": obj.line_number
            }

        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


class CustomDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        if 'faults' not in obj and "contexts" not in obj:
            return obj

        faults = []
        for item in obj["faults"]:
            faults.append(
                FaultInfo(item["category"], item["context"], item["source"], item["line_number"]))

        contexts = []
        for item in obj["contexts"]:
            contexts.append(
                FaultInfo(item["category"], item["context"], item["source"], item["line_number"]))

        return LogFileSummary(faults, contexts)


class LogFileSummary:
    """Summary of all faults, and additional context information in a log file.
    A fault is something like "Bad Exit Code" and context will be "Segmentation Fault".
    """

    def __init__(self, faults, contexts):
        self.faults = faults
        self.contexts = contexts

    def get_faults(self):
        return self.faults

    def get_contexts(self):
        return self.contexts
