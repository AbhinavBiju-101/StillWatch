"""Start the development server:  python run.py
(Database setup commands still use the Flask CLI: python -m flask --app app init-db)"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
