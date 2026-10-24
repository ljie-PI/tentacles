"""Validate Decisions questions and normalize answers without logging payloads."""
from __future__ import annotations

import json
import math
import sys
from typing import Any


def validate_questions(questions: dict[str, Any]) -> None:
    if not questions:
        raise ValueError('at least one decision question is required')
    for name, question in questions.items():
        if not isinstance(name, str) or not name or not isinstance(question, dict):
            raise ValueError('invalid decision question')
        kind = question.get('type')
        if kind not in {'noul', 'choice', 'score'} or not isinstance(question.get('instructions'), (str, dict, list)):
            raise ValueError('invalid decision question type or instructions')
        criteria = question.get('criteria')
        if kind == 'noul' and criteria is not None and (
                not isinstance(criteria, dict) or set(criteria) != {'true', 'false'}):
            raise ValueError('noul criteria require true and false')
        if kind == 'choice' and (not isinstance(criteria, dict) or not criteria or
                                 not all(isinstance(key, str) for key in criteria)):
            raise ValueError('choice criteria require named options')
        if kind == 'score' and (not isinstance(criteria, list) or not criteria):
            raise ValueError('score criteria require an ordered scale')
    json.dumps(questions, allow_nan=False)


def _number(value: Any, upper: float = 1) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not 0 <= value <= upper or not math.isfinite(value)):
        raise ValueError('invalid decision number')
    return float(value)


def normalize_answers(data: Any, questions: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError('decision answers must be an object')
    normalized = {}
    for name, question in questions.items():
        answer = data.get(name)
        kind = question['type']
        if not isinstance(answer, dict) or answer.get('type') != kind:
            raise ValueError('missing or mismatched decision answer')
        parsed: dict[str, Any] = {'type': kind}
        if kind == 'noul':
            parsed['noul'] = _number(answer.get('noul'))
        elif kind == 'choice':
            selected = answer.get('choice')
            if not isinstance(selected, str) or selected not in question['criteria']:
                raise ValueError('decision selected an unknown option')
            parsed['choice'] = selected
        else:
            maximum = len(question['criteria']) - 1
            raw = _number(answer.get('score'), maximum)
            parsed.update(raw_score=raw, score=raw / max(1, maximum))
            if 'legend' in answer:
                if not isinstance(answer['legend'], dict):
                    raise ValueError('invalid score legend')
                parsed['legend'] = answer['legend']
        if 'confidence' in answer:
            parsed['confidence'] = _number(answer['confidence'])
        if 'probabilities' in answer:
            probabilities = answer['probabilities']
            if not isinstance(probabilities, dict) or not all(isinstance(key, str) for key in probabilities):
                raise ValueError('invalid decision probabilities')
            allowed = question['criteria'].keys() if kind == 'choice' else {
                str(i) for i in range(len(question.get('criteria', [])))}
            if kind != 'noul' and not set(probabilities).issubset(allowed):
                raise ValueError('probabilities contain unknown options')
            parsed['probabilities'] = {key: _number(value) for key, value in probabilities.items()}
        normalized[name] = parsed
    json.dumps(normalized, allow_nan=False)
    return normalized


def response_metadata(data: dict[str, Any], fallback_model: str) -> tuple[dict[str, Any], bool]:
    """Keep independently valid billing metadata even when another field is bad."""
    model = data.get('model')
    valid = isinstance(model, str) and bool(model.strip())
    usage = data.get('usage')
    if not isinstance(usage, dict):
        usage, valid = {}, False
    safe: dict[str, Any] = {}
    for key in ('input_tokens', 'output_tokens'):
        value = usage.get(key)
        if type(value) is not int or value < 0:
            safe[key], valid = 0, False
        else:
            safe[key] = value
    cost = usage.get('cost', data.get('cost'))
    if cost is not None:
        try:
            cost = _number(cost, sys.float_info.max)
        except ValueError:
            cost, valid = None, False
    safe['cost'] = cost
    return {'model': model if isinstance(model, str) and model.strip() else fallback_model,
            'usage': safe}, valid
