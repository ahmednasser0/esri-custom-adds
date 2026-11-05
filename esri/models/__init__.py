from . import esri_config
from . import models
# Before deduction and the rest: they inherit the mixin it defines, and Odoo
# builds the models in the order the files are read, so the mixin has to be in
# the registry first.
from . import time_off_balance
from . import deduction
from . import Employee_HR
# from . import allocation
from . import overtime
from . import daily_report
from . import hr_leave_sync
from . import public_holiday_sync
from . import attendance_report
