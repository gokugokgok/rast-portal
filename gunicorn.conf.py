import os

bind = '0.0.0.0:' + os.environ.get('PORT', '10000')
workers = 1
threads = 16
timeout = 45
# Access logging stays disabled; do not enable --access-logfile.
accesslog = None
errorlog = '-'
loglevel = 'warning'
capture_output = False
