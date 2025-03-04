FIRST_DAY_OF_NCC_WEEKS = (2025, 6, 30)

# What index is that within the whole year?
from datetime import datetime

def day_of_year(date_tuple):
    """
    Calculates the day of the year for a given date string.

    Args:
    date_string: The date as a string (e.g., "2025-03-03").
    date_format: The format of the date string (e.g., "%Y-%m-%d").

    Returns:
    The day of the year as an integer (1-365 or 1-366 for leap years).
    """
    date_string = f"{date_tuple[0]:4d}-{date_tuple[1]:02d}-{date_tuple[2]:02d}"
    date_object = datetime.strptime(date_string, "%Y-%m-%d")
    return date_object.timetuple().tm_yday

# print(day_of_year((2025, 6,30)))

def vacation_date_to_week_index(vacation_date):
    """
    :param vacation_date: the (month, day) desired off (usually in written communication as "the week of X"
    :return: the week-index of the NCC year containing it.
    """
    first = day_of_year(FIRST_DAY_OF_NCC_WEEKS)
    n = day_of_year((vacation_date))
    if vacation_date[0] > FIRST_DAY_OF_NCC_WEEKS[0]: # next-year date
        return (day_of_year((FIRST_DAY_OF_NCC_WEEKS[0], 12, 31)) - first + n) // 7
    else:
        return (n - first) // 7

# print(vacation_date_to_week_index((2026, 1, 5)))
# assert vacation_date_to_week_index((2025, 12, 25)) == 25
# assert vacation_date_to_week_index((2025, 10, 6)) == 14
# assert vacation_date_to_week_index((2025, 10, 12)) == 14
# assert vacation_date_to_week_index((2026, 1, 1)) == 26
# # ok it's the
# assert vacation_date_to_week_index((2026, 1, 5)) == 27
# assert vacation_date_to_week_index((2026, 3, 2)) == 35
