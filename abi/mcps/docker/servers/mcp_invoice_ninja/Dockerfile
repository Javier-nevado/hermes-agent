FROM python:3.13-alpine
LABEL maintainer="Opteia <abi@opteia.com>"
LABEL org.opteia.mcp="mcp_invoice_ninja"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY http_adapter.py .

EXPOSE 5112
ENV MCP_PORT=5112

CMD ["python", "http_adapter.py", "server.server", "InvoiceNinjaMCPServer", "--port", "5112"]
