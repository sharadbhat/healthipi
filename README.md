# HealthiPi

A small personal project that shows today's Google Health data on an 800x480 Inky display.

It displays steps, sleep, resting heart rate, HRV, calories, and Active Zone Minutes. RHR and HRV trends compare today with yesterday. Everything lives in [healthipi.py](healthipi.py).

The display has a large steps panel, sleep and calories on the right, and RHR, HRV, and Active Zone Minutes below. Sleep bars show the last seven nights; RHR/HRV charts show 14 days. Missing readings stay blank. Active Zone Minutes shows progress toward 22 minutes.

## Setup

On the Raspberry Pi, install Pimoroni's Inky library first:

```bash
git clone https://github.com/pimoroni/inky.git
cd inky
./install.sh
```

Then install this project's dependencies in the Pimoroni virtual environment:

```bash
source ~/.virtualenvs/pimoroni/bin/activate
cd ~/healthipi
python -m pip install -r requirements.txt
```

Put your OAuth client file at `client_secret.json`, then authorize once:

```bash
python healthipi.py --auth
```

This creates `token.json`. Google refreshes it automatically, so normal daily runs do not ask you to sign in again. Keep both JSON files private.

## Run it

Preview real data without touching the display:

```bash
python healthipi.py --dry-run
```

Try the layout without Google credentials:

```bash
python healthipi.py --mock --dry-run
```

Update the Inky display:

```bash
python healthipi.py
```

Every run also writes `healthipi_output.png`.

To change the timezone or goals, edit `TIMEZONE`, `STEPS_GOAL` (11,000), `CALORIES_GOAL` (3,000), and `ACTIVE_ZONE_MINUTES_GOAL` (22) near the top of `healthipi.py`.

## Run automatically

The included timer runs at 00 and 30 minutes past every hour:

```bash
sudo cp systemd_scripts/healthipi.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now healthipi.timer
```

The service assumes this repo is at `/home/pi/healthipi` and Pimoroni's environment is at `/home/pi/.virtualenvs/pimoroni`.
