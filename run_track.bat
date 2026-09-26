@echo off
cd /d "C:\Users\Lol\Downloads\Project\Price tracker e-commerce\price-tracker"
call venv\Scripts\activate.bat
python main.py track >> data\logs\track.log 2>&1