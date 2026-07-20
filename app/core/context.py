import contextvars
import logging

trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_ctx.get()
        return True
