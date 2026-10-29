"""Runs Athena queries for the api and enforcer functions: several at once, rows back as dicts."""

import os
import time

import boto3

athena = boto3.client("athena")


def run(queries: dict, seconds: float = 22) -> dict:
    """{key: (sql, [params])} -> {key: [row dicts]}. All start together, so a page costs its slowest query.

    Athena runs a limited number of queries at once per account and region (20 in many), shared by every page and
    the enforcer: a start it refuses for that is tried again until the time is up. If one query fails, or the time
    runs out, the others are stopped, so nothing keeps running (and costing) after we've given up. The default
    leaves the dashboard's API time to read the results within API Gateway's 29 seconds."""
    deadline = time.monotonic() + seconds
    started = {}
    try:
        for key, (sql, params) in queries.items():
            request = {"QueryString": sql, "WorkGroup": os.environ["WORKGROUP"],
                       "QueryExecutionContext": {"Database": os.environ["DATABASE"]}}
            if params:
                request["ExecutionParameters"] = [quote(p) for p in params]
            started[key] = start(request, deadline)

        pending = set(started.values())
        while pending:
            if time.monotonic() > deadline:
                raise TimeoutError("Athena took too long: try a shorter range")
            time.sleep(0.5)
            for query_id in list(pending):
                status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
                if status["State"] == "SUCCEEDED":
                    pending.discard(query_id)
                elif status["State"] in ("FAILED", "CANCELLED"):
                    raise RuntimeError(status.get("StateChangeReason", status["State"]))
        return {key: rows(query_id) for key, query_id in started.items()}
    except Exception:
        for query_id in started.values():
            try:
                athena.stop_query_execution(QueryExecutionId=query_id)  # finished ones just say so
            except athena.exceptions.ClientError:
                pass
        raise


def start(request: dict, deadline: float) -> str:
    wait = 0.5
    while True:
        try:
            return athena.start_query_execution(**request)["QueryExecutionId"]
        except athena.exceptions.TooManyRequestsException:
            if time.monotonic() + wait > deadline:
                raise TimeoutError("Athena is busy with other queries: try again in a moment") from None
            time.sleep(wait)
            wait = min(wait * 2, 4)


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
