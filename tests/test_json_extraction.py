import json

from legit.model_runner import _extract_json


def test_brackets_inside_quoted_review_text_are_not_structure():
    payload = {"comment": 'Remove the extra } and escape the "quote"', "examples": ["["]}
    encoded = json.dumps(payload)
    assert json.loads(_extract_json("Here is the review: " + encoded + "\nDone")) == payload


def test_array_with_quoted_brackets_remains_an_array():
    payload = [{"comment": "This ] is part of the code"}]
    assert json.loads(_extract_json("Result: " + json.dumps(payload))) == payload
