import os
import subprocess
import requests
import logging
from requests.exceptions import ConnectionError, Timeout, RequestException
from urllib3.exceptions import MaxRetryError, NameResolutionError
from nyct_gtfs import NYCTFeed
from datetime import datetime as dt
import math
import time
from gpiozero import Button
onoff_sw = Button(25, pull_up=True)
feed1 = NYCTFeed("G")
feed2 = NYCTFeed("F")
trains1 = feed1.filter_trips(line_id=["G"], headed_for_stop_id=["F25N"], underway=True)
trains2 = feed2.filter_trips(line_id=["F"], headed_for_stop_id=["F25N"], underway=True)
current_time = dt.now()
which_train = 0
next_trains = {}
delayed = ""

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='mta_feed.log' 
)
logger = logging.getLogger('mta_feed')

# Set up log rotation to prevent log file bloat
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    'mta_feed.log', 
    maxBytes=1024*1024,  # 1 MB per file
    backupCount=5  # Keep 5 backup files max
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Replace the default file handler with our rotating one
for handler in logger.root.handlers[:]:
    if isinstance(handler, logging.FileHandler):
        logger.root.removeHandler(handler)
logger.root.addHandler(file_handler)

def refresh_feed_with_retry(feed, max_retries=3, retry_delay=5):
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            feed.refresh()
            return True  # Success
        except (ConnectionError, Timeout, MaxRetryError, NameResolutionError) as e:
            retry_count += 1
            logger.warning(f"Connection error (attempt {retry_count}/{max_retries}): {str(e)}")
            
            if retry_count < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect after {max_retries} attempts. Using cached data if available.")
                return False
        except RequestException as e:
            logger.error(f"Request error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error refreshing feed: {str(e)}")
            return False
    
    return False

def start_display():
    function_call_path = "./rpi-rgb-led-matrix/examples-api-use/text-example-mod"
    font_path = "./rpi-rgb-led-matrix/fonts/6x13.bdf"

    command = [
        'sudo',
        function_call_path,
        '--led-rows=32',
        '--led-cols=64',
        '-f',
        font_path,
        '--led-gpio-mapping=adafruit-hat',
        '-x',
        '4',
        '-y',
        '2'
    ]
    try:
        subprocess.Popen(command)
    except Exception as e:
        print(f"Error running command: {e}")
        print(f"Command was: {' '.join(command)}")

def setColor(train):
    rgb_color = "0,0,0"
    if train == "G":
        rgb_color = "40,170,5"
    elif train == "F":
        rgb_color = "190,50,5"
    return rgb_color

def update_display(msg1, train1, msg2, train2):
    FIFO_PATH = "/tmp/led_matrix_fifo"
    rgb_color1 = setColor(train1)
    rgb_color2 = setColor(train2)
    message = f"{msg1}|{msg2}|{rgb_color1}|{rgb_color2}"
    # Write to the FIFO
    try:
        with open(FIFO_PATH, 'w') as fifo:
            fifo.write(message)
            fifo.flush()  # Ensure the message is sent immediately
    except IOError as e:
        print(f"Error writing to FIFO: {e}")

def find_trains(train_feed):
    global next_trains, current_time, delayed
    for train in train_feed:
        counter = 0
        for i in range(len(train.stop_time_updates) -1):
            if train.stop_time_updates[i].stop_name == "15 St-Prospect Park":
                break
            counter  += 1
        if train.has_delay_alert:
            delayed = "DEL"
        train_arrival = train.stop_time_updates[counter].arrival
        current_time = current_time.replace(microsecond=0)
        eta = train_arrival - current_time
        min = math.floor(eta.seconds/60)
        if min > 1000:
            min = 0
        if min in next_trains:
            min += 1
        next_trains.update({min: train.route_id})

def refresh_feeds(feed):
    global trains1, trains2, next_trains
    try:
        # Try to refresh the feed with retry logic
        success = refresh_feed_with_retry(feed)
        if success:
            # Process the data as normal
            if feed == feed1:
                trains1 = feed.filter_trips(line_id=["G"], headed_for_stop_id=["F25N"], underway=True)
                find_trains(trains1)
            if feed == feed2:
                trains2 = feed.filter_trips(line_id=["F"], headed_for_stop_id=["F25N"], underway=True)
                find_trains(trains2)
            if 0 in next_trains:
                next_trains.pop(0)
            if time.time() % 3600 < 10:  # Log success roughly once per hour
                logger.info("Successfully processed feed data")
        else:
            # Fall back to cached data or alternative behavior
            logger.warning("Using previous feed data or fallback behavior")
            # ... your fallback behavior here ...
    except Exception as e:
        # Catch any other unexpected errors to prevent app from crashing
        logger.error(f"Error in main loop: {str(e)}")
        time.sleep(10)  # Wait before retrying

start_display()

def main():
    global current_time, next_trains, feed1, feed2, trains1, trains2, which_train, delayed

    while True:
        current_time = dt.now()
        next_trains.clear()
        refresh_feeds(feed1)
        refresh_feeds(feed2)
        sorted_trains = sorted(next_trains.items())
        if len(sorted_trains) == 0:
            sorted_trains = [('','')]
        if which_train >= len(sorted_trains) or which_train > 3:
            which_train = 0
        # logger.info(f"Sorted trains: {sorted_trains} ")
        message1 = ""
        line1 = ""
        order1 = str(which_train + 1)
        message1 = order1 + ")"+ str(sorted_trains[which_train][1]) + " " + str(sorted_trains[which_train][0]) + "min "+ delayed
        line1 = sorted_trains[which_train][1]
        message2 = ""
        line2 = ""
        order2 = str(which_train + 2)
        if which_train < len(sorted_trains) - 1:
            message2 = order2 + ")"+ str(sorted_trains[which_train + 1][1]) + " " + str(sorted_trains[which_train + 1][0]) + "min "+ delayed
            line2 = sorted_trains[which_train + 1][1]
        # logger.info(f"{message1}{line1} {message2}{line2}")
        if not onoff_sw.is_pressed:
            message1 = ""
            message2 = ""
            line1 = ""
            line2 = ""
        update_display(message1, line1, message2, line2)
        which_train += 2
        time.sleep(3)

if __name__ == "__main__":
    main() 
