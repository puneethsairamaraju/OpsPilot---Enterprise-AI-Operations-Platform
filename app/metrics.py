from prometheus_client import Counter, Histogram

QUERY_COUNT = Counter("opspilot_queries_total", "AI queries", ["status"])
QUERY_LATENCY = Histogram("opspilot_query_latency_seconds", "Query workflow latency")
INGEST_COUNT = Counter("opspilot_documents_ingested_total", "Documents ingested")
APPROVAL_COUNT = Counter("opspilot_approval_decisions_total", "Approval decisions", ["decision"])

