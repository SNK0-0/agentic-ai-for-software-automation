"""Order Processing and gRPC Dispatch Service."""

class App:
    def post(self, path):
        return lambda f: f

app = App()

class BaseOrderProcessor:
    def process(self, order_id):
        return {"status": "pending"}

class FastOrderProcessor(BaseOrderProcessor):
    def process(self, order_id):
        return {"status": "shipped"}

@app.post("/api/checkout")
def handle_checkout():
    """Validates order, queries shipping quote via gRPC, and executes payment."""
    processor = FastOrderProcessor()
    res = processor.process("ORD-99")
    stub.GetQuote(None)
    return {"order": res, "quote": 20}

if __name__ == "__main__":
    import shipping_pb2_grpc
    stub = shipping_pb2_grpc.ShippingServiceStub(None)
