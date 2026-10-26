from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "RadarOfertas AI - servidor OAuth funcionando!"


@app.route("/oauth/callback")
def oauth_callback():
    return "Callback OAuth recebido!"


if __name__ == "__main__":
    # debug=False: o modo debug permite executar codigo pelo navegador.
    app.run(host="127.0.0.1", port=5000, debug=False)
