"""Minimal Flask-style source fixture used for static cross-service evaluation."""


class DemoApp:
    def get(self, path):
        return lambda handler: handler

    def route(self, path, methods=None):
        return lambda handler: handler


app = DemoApp()


@app.get("/api/greeting")
def get_greeting():
    return {"message": "hello"}


@app.route("/api/echo", methods=["POST"])
def echo():
    return {"accepted": True}
