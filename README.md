# Create virtual environment
python -m venv venv

# Activate venv
source venv/bin/activate

# Install requirements
pip install --upgrade pip
pip install -r requirements.txt

# Run the script
python main.py --web
or
python main.py --report
