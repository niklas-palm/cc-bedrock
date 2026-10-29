"""Runs Athena queries for the api and enforcer functions: several at once, rows back as dicts."""

import os
import time

import boto3

athena = boto3.client("athena")


def run(queries: dict) -> dict:
    """{key: (sql, [params])} -> {key: [row dicts]}. All start together, so a page costs its slowest query."""
    started = {}
    for key, (sql, params) in queries.items():
        request = {"QueryString": sql, "WorkGroup": os.environ["WORKGROUP"],
                   "QueryExecutionContext": {"Database": os.environ["DATABASE"]}}
        if params:
            request["ExecutionParameters"] = [quote(p) for p in params]
        started[key] = athena.start_query_execution(**request)["QueryExecutionId"]

    pending, failed = set(started.values()), {}
    while pending:
        time.sleep(0.3)
        for query_id in list(pending):
            status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
            if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                pending.discard(query_id)
                if status["State"] != "SUCCEEDED":
                    failed[query_id] = status.get("StateChangeReason", status["State"])
    if failed:
        raise RuntimeError("; ".join(failed.values()))
    return {key: rows(query_id) for key, query_id in started.items()}


def quote(value) -> str:
    """Execution parameters are SQL literals: strings quoted (Athena escapes nothing for us), numbers as they are."""
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def rows(query_id: str) -> list:
    result, header = [], None
    for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=query_id):
        for row in page["ResultSet"]["Rows"]:
            values = [cell.get("VarCharValue") for cell in row["Data"]]
            if header is None:
                header = values
            else:
                result.append(dict(zip(header, values, strict=True)))
    return result


def number(value) -> float:
    return float(value) if value not in (None, "") else 0.0
