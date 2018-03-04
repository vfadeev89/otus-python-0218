#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import json
import re
import os
import gzip
import logging
import collections
import time
from datetime import datetime

# log_format ui_short '$remote_addr $remote_user $http_x_real_ip [$time_local] "$request" '
#                     '$status $body_bytes_sent "$http_referer" '
#                     '"$http_user_agent" "$http_x_forwarded_for" "$http_X_REQUEST_ID" "$http_X_RB_USER" '
#                     '$request_time';

config = {
    "REPORT_SIZE": 1000,
    "REPORT_DIR": "./reports",
    "REPORT_TEMPLATE": "report-{date}.html",
    "LOG_DIR": "./log",
    "LOG_NAME_PATTERN": "nginx-access-ui.log",
    "ALLOW_ERRORS_COUNT": 100,
    "TS_FILE": "./log_analyzer.ts"
}
error_count = 0


def track_parsing_error():
    global error_count
    error_count += 1
    logging.info("Incremented error_counter " + str(error_count))
    if error_count >= config["ALLOW_ERRORS_COUNT"]:
        raise RuntimeError("Too many lines in log file are corrupted.")


def load_config(path):
    result = config.copy()
    if path is not None:
        try:
            with open(path, 'r') as f:
                try:
                    data = json.load(f)
                    for key in data:
                        result[key] = data[key]
                except ValueError as value_error:
                    logging.info(
                        "Config file {} has invalid format. Using default config.\n{}".format(path, value_error))
        except IOError as io_error:
            logging.info("Invalid path to config file. Using default config.\n{}".format(io_error))
    else:
        logging.info("Path to config file must be not None. Using default config.")
    return result


def simple_search(something, in_source):
    return something in in_source


def get_latest_log_file_path(log_dir, name_pattern):
    list_of_files = [f for f in os.listdir(log_dir) if simple_search(name_pattern, f)]
    if not list_of_files:
        raise StandardError("Log folder {} is empty.".format(log_dir))

    latest_file_name = max(list_of_files, key=lambda f: re.findall("(\d{8})", f))
    return os.path.join(log_dir, latest_file_name)


def xreadlines(path):
    if path.endswith(".gz"):
        log_file = gzip.open(path, 'rb')
    else:
        log_file = open(path, 'r')
    for line in log_file:
        yield line
    log_file.close()


def apply_filters(source, filters):
    for string_dict in source:
        result = {}
        for k in string_dict:
            for f in filters:
                if k == f["key"]:
                    try:
                        result[k] = f["func"](string_dict[k])
                    except Exception:
                        logging.error("Error occurs in: {}".format(string_dict))
                        track_parsing_error()

        if result:
            yield result


def parse_log(path):
    pattern = re.compile(
        r"(?P<remote_addr>[\d\.]+)\s"
        r"(?P<remote_user>\S*)\s+"
        r"(?P<http_x_real_ip>\S*)\s"
        r"\[(?P<time_local>.*?)\]\s"
        r'"(?P<request>.*?)"\s'
        r"(?P<status>\d+)\s"
        r"(?P<body_bytes_sent>\S*)\s"
        r'"(?P<http_referer>.*?)"\s'
        r'"(?P<http_user_agent>.*?)"\s'
        r'"(?P<http_x_forwarded_for>.*?)"\s'
        r'"(?P<http_X_REQUEST_ID>.*?)"\s'
        r'"(?P<http_X_RB_USER>.*?)"\s'
        r"(?P<request_time>\d+\.\d+)\s*"
    )
    log_lines = xreadlines(path)
    parsed_line_dict = (pattern.match(line).groupdict() for line in log_lines)

    requests_filter = dict(key="request", func=lambda req: req.split(" ")[1])
    requests_time_filter = dict(key="request_time", func=float)
    filters = [requests_filter, requests_time_filter]

    return apply_filters(parsed_line_dict, filters)


def median(lst):
    quotient, remainder = divmod(len(lst), 2)
    if remainder:
        return lst[quotient]
    return float(sum(lst[quotient - 1:quotient + 1]) / 2)


def analyze_log(log_dict, report_size):
    urls = collections.defaultdict(list)

    for log_line in log_dict:
        urls[log_line.get("request")].append(log_line["request_time"])

    total_requests_count = total_requests_time = 0
    for v in urls.itervalues():
        total_requests_count += len(v)
        total_requests_time += sum(v)

    report_data = []
    for url, times_list in urls.iteritems():
        times_list.sort()
        report_data.append({
            'url': url,
            'count': len(times_list),
            'count_perc': round(100 * len(times_list) / float(total_requests_count), 3),
            'time_sum': round(sum(times_list), 3),
            'time_perc': round(100 * sum(times_list) / total_requests_time, 3),
            'time_avg': round(sum(times_list) / len(times_list), 3),
            'time_max': round(max(times_list), 3),
            'time_med': round(median(times_list), 3)
        })

    report_data.sort(key=lambda x: x['time_sum'], reverse=True)
    return report_data[:report_size]


def save_report(path, report_template, report_date, data):
    with open(os.path.join(path, report_template), 'r') as f:
        f_data = f.read()

    result = f_data.replace("$table_json", json.dumps(data))
    with open(os.path.join(path, report_template.format(date=report_date)), 'w') as f:
        f.write(result)


def get_report_date(log_file_name):
    date_string = re.findall("(\d{8})", log_file_name)[0]
    return datetime.strptime(date_string, "%Y%m%d").strftime("%Y.%m.%d")


def update_ts_file(path):
    with open(path, 'w') as f:
        f.write(str(time.mktime(datetime.now().timetuple())))


def is_report_exists(reports_dir, report_template, log_time):
    report_name = os.path.join(reports_dir, report_template.format(date=get_report_date(log_time)))
    return os.path.isfile(report_name)


def setup_logging(path_to_save=None):
    logging.basicConfig(format="[%(asctime)s] %(levelname).1s %(message)s",
                        datefmt="%Y.%m.%d %H:%M:%S",
                        filename=os.path.join(path_to_save),
                        filemode='a',
                        level=logging.INFO)


def main(args):
    try:
        setup_logging(args.logging_file)
        config_file = load_config(args.custom_config)

        try:
            latest_log_file_path = get_latest_log_file_path(config_file["LOG_DIR"], config_file["LOG_NAME_PATTERN"])
        except StandardError as error:
            logging.info(error.message)
            return

        reports_dir = config_file["REPORT_DIR"]
        report_template = config_file["REPORT_TEMPLATE"]

        if is_report_exists(reports_dir, report_template, latest_log_file_path):
            logging.info("Report for {} already exists.".format(latest_log_file_path))
            return

        logging.info("Starting analysing log file: {}".format(latest_log_file_path))
        try:
            parsed_log = parse_log(latest_log_file_path)
            report_data = analyze_log(parsed_log, config_file["REPORT_SIZE"])
            save_report(reports_dir, report_template,
                        get_report_date(latest_log_file_path), report_data)
            update_ts_file(config_file["TS_FILE"])

            logging.info("All done!")
        except RuntimeError as error:
            logging.error("Script stopped abnormally by error: {}".format(error.message))
    except BaseException as base_e:
        logging.exception(base_e)


def parse_sys_args():
    parser = argparse.ArgumentParser(description="Nginx log file analyzer")
    parser.add_argument("-c", "--config", dest="custom_config", help="custom config file")
    parser.add_argument("-l", "--log", dest="logging_file", help="path to save logging file")
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_sys_args())