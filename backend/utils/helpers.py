from bson import ObjectId
from datetime import datetime


def serialize_doc(doc: dict) -> dict:
    """
    Convert MongoDB document to JSON-serializable dict
    Converts ObjectIds to strings, _id to id
    """
    if not doc:
        return None

    # Convert _id to id
    if "_id" in doc:
        doc["id"] = str(doc["_id"])
        del doc["_id"]

    # Convert all ObjectId fields to strings
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            doc[key] = str(value)
        elif isinstance(value, list):
            # Handle arrays - recurse into dicts, convert ObjectIds and datetimes
            result = []
            for v in value:
                if isinstance(v, dict):
                    result.append(serialize_doc(v))
                elif isinstance(v, ObjectId):
                    result.append(str(v))
                elif isinstance(v, datetime):
                    iso_string = v.isoformat()
                    if not iso_string.endswith('Z') and v.tzinfo is None:
                        iso_string += 'Z'
                    result.append(iso_string)
                else:
                    result.append(v)
            doc[key] = result
        elif isinstance(value, dict):
            # Recursive for nested documents
            doc[key] = serialize_doc(value)
        elif isinstance(value, datetime):
            # Convert datetime to ISO string with UTC timezone indicator
            # Add 'Z' suffix if timezone-naive (assumes UTC)
            iso_string = value.isoformat()
            if not iso_string.endswith('Z') and value.tzinfo is None:
                iso_string += 'Z'
            doc[key] = iso_string

    return doc


def serialize_docs(docs: list) -> list:
    """Convert list of MongoDB documents"""
    return [serialize_doc(doc.copy()) for doc in docs]
