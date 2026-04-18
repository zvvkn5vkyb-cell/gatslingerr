# gatslingerr

IBKR + Fund Accounting Dashboard built with Streamlit.

## Setup

### Prerequisites

- Python 3.10+
- Docker (for PostgreSQL)
- Interactive Brokers TWS or IB Gateway

### Install dependencies

**Windows (PowerShell) / macOS / Linux:**

```powershell
pip install -r requirements.txt
```

> **Windows users:** Do NOT paste Python code directly into PowerShell.
> PowerShell is not a Python interpreter. Python statements like
> `from openai import OpenAI` must be placed in a `.py` file and run with:
>
> ```powershell
> python your_script.py
> ```
>
> To run a quick one-liner use the `-c` flag:
>
> ```powershell
> python -c "from openai import OpenAI; print('ok')"
> ```

### Start the database

```powershell
cd financial-db
docker-compose up -d
```

### Run the dashboard

```powershell
streamlit run app.py
```

### Run the API server

```powershell
python api.py
```

## Running Python scripts on Windows

If you see this error in PowerShell:

```
The 'from' keyword is not supported in this version of the language.
```

It means you pasted Python code into PowerShell instead of a Python file.
Save your code to a `.py` file and run it with `python filename.py`.
