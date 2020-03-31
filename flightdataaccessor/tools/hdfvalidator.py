#!/usr/bin/env python
'''
HDFValidator checks flight data, stored in a HDF5 file format, is in a
compatible structure meeting POLARIS pre-analysis specification.
'''
import argparse
import logging
import os
from math import ceil

import h5py
import numpy as np

from analysis_engine.utils import list_parameters
from flightdatautilities.validation_tools.param_validator import (
    check_for_core_parameters,
    validate_arinc_429,
    validate_data_type,
    validate_dataset,
    validate_frequency,
    validate_lfl,
    validate_name,
    validate_source_name,
    validate_supf_offset,
    validate_units,
    validate_values_mapping,
)

import flightdataaccessor as fda
from flightdataaccessor.tools.parameter_lists import PARAMETERS_FROM_FILES

logger = logging.getLogger(__name__)


class StoppedOnFirstError(Exception):
    """ Exception class used if user wants to stop upon the first error."""
    pass


class HDFValidatorHandler(logging.Handler):
    """
    A handler to raise stop on the first error log.
    """
    def __init__(self, stop_on_error=None):
        super(HDFValidatorHandler, self).__init__()
        self.stop_on_error = stop_on_error
        self.errors = 0
        self.warnings = 0

    def emit(self, record):
        ''' Log message. Then increment counter if message is a warning or
            error. Error count includes critical errors. '''
        if record.levelno >= logging.ERROR:
            self.errors += 1
        elif record.levelno == logging.WARN:
            self.warnings += 1
        if self.stop_on_error and record.levelno >= logging.ERROR:
            raise StoppedOnFirstError()

    def get_error_counts(self):
        ''' returns the number of warnings and errors logged.'''
        return {'warnings': self.warnings, 'errors': self.errors}


def log(log_records):
    '''Send to logger the list of LogRecords'''
    for log_record in log_records:
        logger.handle(log_record)


# -----------------------------------------------------------------------------


def log_title(title, line='=', section=True):
    """Add visual breaks in the logging for main sections."""
    if section:
        logger.info("%s", '_' * 80)
    logger.info(title)
    logger.info("%s", line * len(title))


def log_subtitle(subtitle):
    """Add visual breaks in the logging for sub sections."""
    log_title(subtitle, line='-', section=False)


# =============================================================================
#   Parameter's Attributes
# =============================================================================
def validate_parameters(hdf, helicopter=False, names=None, states=False):
    """
    Iterates through all the parameters within the 'series' namespace and
    validates:
        Matches a POLARIS recognised parameter
        Attributes
        Data
    """
    log_title("Checking Parameters")
    hdf_parameters = hdf.keys()
    log(check_for_core_parameters(hdf_parameters, helicopter))
    if names:
        hdf_parameters = [param for param in hdf_parameters if param in names]
    for name in hdf_parameters:
        try:
            parameter = hdf.get_parameter(name)
        except np.ma.core.MaskError as err:
            logger.error("MaskError: Cannot get parameter '%s' (%s).",
                         name, err)
            continue
        log_title("Checking Parameter: '%s'" % (name, ))
        validate_parameter_attributes(hdf, name, parameter, states=states)
        validate_parameters_dataset(hdf, name, parameter)
    return


def validate_parameter_attributes(hdf, name, parameter, states=False):
    """Validates all parameter attributes."""
    log_subtitle("Checking Attribute for Parameter: %s" % (name, ))
    param_attrs = hdf.file['/series/' + name].attrs.keys()
    expected_attrs = ['data_type', 'frequency', 'lfl', 'name', 'supf_offset']
    if parameter.data_type not in ('Discrete', 'Multi-state', 'ASCII',
                                   'Enumerated Discrete', 'Derived Multistate'):
        expected_attrs.append('units')
    for attr in expected_attrs:
        if attr not in param_attrs:
            logger.error("Parameter attribute '%s' not present for '%s' "
                         "and is Required.", attr, name)
    log(validate_arinc_429(parameter))
    log(validate_source_name(parameter))
    log(validate_supf_offset(parameter))
    log(validate_values_mapping(parameter, states=states))
    if 'data_type' in param_attrs:
        log(validate_data_type(parameter))
    if 'frequency' in param_attrs:
        log(validate_frequency(parameter))
        validate_hdf_frequency(hdf, parameter)
    if 'lfl' in param_attrs:
        log(validate_lfl(parameter))
    if 'name' in param_attrs:
        log(validate_name(parameter, name))
    if 'units' in param_attrs:
        log(validate_units(parameter))


def validate_parameters_dataset(hdf, name, parameter):
    """Validates all parameter datasets."""
    log_subtitle("Checking dataset for Parameter: %s" % (name, ))
    expected_size_check(hdf, parameter)
    log(validate_dataset(parameter))


# =============================================================================
#   Parameter's Attributes
# =============================================================================


def validate_hdf_frequency(hdf, parameter):
    """
    Checks if the parameter attribute frequency is listed in the root
    attribute frequencies.
    """
    if hdf.frequencies is not None  and parameter.frequency is not None:
        if 'array' in type(hdf.frequencies).__name__:
            if parameter.frequency not in hdf.frequencies:
                logger.warning("'frequency': Value not in the Root "
                            "attribute list of frequenices.")
        elif parameter.frequency != hdf.frequencies:
            logger.warning("'frequency': Value not in the Root "
                        "attribute list of frequenices.")


def expected_size_check(hdf, parameter):
    boundary = 64.0 if hdf.superframe_present else 4.0
    frame = 'super frame' if hdf.superframe_present else 'frame'
    logger.info('Boundary size is %s for a %s.', boundary, frame)
    # Expected size of the data is duration * the parameter's frequency,
    # includes any padding required to the next frame/super frame boundary
    if hdf.duration and parameter.frequency:
        expected_data_size = \
            ceil(hdf.duration / boundary) * boundary * parameter.frequency
    else:
        logger.error("%s: Not enough information to calculate expected data "
                     "size. Duration: %s, Parameter Frequency: %s",
                     parameter.name,
                     'None' if hdf.duration is None else hdf.duration,
                     'None' if parameter.frequency is None else
                     parameter.frequency)
        return

    logger.info("Checking parameters dataset size against expected frame "
                "aligned size of %s.", int(expected_data_size))
    logger.debug("Calculated: ceil(Duration(%s) / Boundary(%s)) * "
                 "Boundary(%s) * Parameter Frequency (%s) = %s.",
                 hdf.duration, boundary, boundary, parameter.frequency,
                 expected_data_size)

    if expected_data_size != parameter.array.size:
        logger.error("The data size of '%s' is %s and different to the "
                     "expected frame aligned size of %s. The data needs "
                     "padding by %s extra masked elements to align to the "
                     "next frame boundary.", parameter.name,
                     parameter.array.size, int(expected_data_size),
                     int(expected_data_size)-parameter.array.size)
    else:
        logger.info("Data size of '%s' is of the expected size of %s.",
                    parameter.name, int(expected_data_size))



def validate_namespace(hdf5):
    '''Uses h5py functions to verify what is stored on the root group.'''
    found = ''
    log_title("Checking for the namespace 'series' group on root")
    if 'series' in hdf5.keys():
        logger.info("Found the POLARIS namespace 'series' on root.")
        found = 'series'
    else:
        found = [g for g in hdf5.keys() if 'series' in g.lower()]
        if found:
            # series found but in the wrong case.
            logger.error("Namespace '%s' found, but needs to be in "
                         "lower case.", found)
        else:
            logger.error("Namespace 'series' was not found on root.")

    logger.info("Checking for other namespace groups on root.")
    group_num = len(hdf5.keys())

    show_groups = False
    if group_num == 1 and 'series' in found:
        logger.info("Namespace 'series' is the only group on root.")
    elif group_num == 1 and 'series' not in found:
        logger.error("Only one namespace on root,but not the required "
                     "'series' namespace.")
        show_groups = True
    elif group_num == 0:
        logger.error("No namespace groups found in the file.")
    elif group_num > 1 and 'series' in found:
        logger.warning("Namespace 'series' found, along with %s addtional "
                    "groups. If these are parmeters and required by Polaris "
                    "for analysis, they must be stored within 'series'.",
                    group_num - 1)
        show_groups = True
    elif group_num > 1:
        logger.error("There are %s namespace groups on root, "
                     "but not the required 'series' namespace. If these are "
                     "parmeters and required by Polaris for analysis, they "
                     "must be stored within 'series'.", group_num)
        show_groups = True
    if show_groups:
        logger.debug("The following namespace groups are on root: %s",
                     [g for g in hdf5.keys() if 'series' not in g])



# =============================================================================
#   Root Attributes
# =============================================================================
def validate_root_attribute(hdf):
    """Validates all the root attributes."""
    log_title("Checking the Root attributes")
    root_attrs = hdf.file.attrs.keys()
    for attr in ['duration', 'reliable_frame_counter',
                 'reliable_subframe_counter',]:
        if attr not in root_attrs:
            logger.error("Root attribute '%s' not present and is required.",
                         attr)
    if 'duration' in root_attrs:
        validate_duration_attribute(hdf)
    validate_frequencies_attribute(hdf)
    if 'reliable_frame_counter' in root_attrs:
        validate_reliable_frame_counter_attribute(hdf)
    if 'reliable_subframe_counter' in root_attrs:
        validate_reliable_subframe_counter_attribute(hdf)
    validate_start_timestamp_attribute(hdf)
    validate_superframe_present_attribute(hdf)


def validate_duration_attribute(hdf):
    """
    Check if the root attribute duration exists (It is required)
    and report the value.
    """
    logger.info("Checking Root Attribute: duration")
    if hdf.duration:
        logger.info("'duration': Attribute present with a value of %s.",
                    hdf.file.attrs['duration'])
        if 'int' in type(hdf.file.attrs['duration']).__name__:
            logger.debug("'duration': Attribute is an int.")
        else:
            logger.error("'duration': Attribute is not an int. Type "
                         "reported as '%s'.",
                         type(hdf.file.attrs['duration']).__name__)
    else:
        logger.error("'duration': No root attribrute found. This is a "
                     "required attribute.")


def validate_frequencies_attribute(hdf):
    """
        Check if the root attribute frequencies exists (It is optional).
        Report all the values and if the list covers all frequencies used
        by the store parameters.
    """
    logger.info("Checking Root Attribute: 'frequencies'")
    if hdf.frequencies is None:
        logger.info("'frequencies': Attribute not present and is optional.")
        return

    logger.info("'frequencies': Attribute present.")
    name = type(hdf.frequencies).__name__
    if 'array' in name or 'list' in name:
        floatcount = 0
        rootfreq = set(list(hdf.frequencies))
        for value in hdf.frequencies:
            if 'float' in type(value).__name__:
                floatcount += 1
            else:
                logger.error("'frequencies': Value %s should be a float.",
                             value)
        if floatcount == len(hdf.frequencies):
            logger.info("'frequencies': All values listed are float values.")
        else:
            logger.error("'frequencies': Not all values are float values.")
    elif 'float' in name:
        logger.info("'frequencies': Value is a float.")
        rootfreq = set([hdf.frequencies])
    else:
        logger.error("'frequencies': Value is not a float.")

    paramsfreq = set([v.frequency for _, v in hdf.items()])
    if rootfreq == paramsfreq:
        logger.info("Root frequency list covers all the frequencies "
                    "used by parameters.")
    elif rootfreq - paramsfreq:
        logger.info("Root frequencies lists has more frequencies than "
                    "used by parameters. Unused frquencies: %s",
                    list(rootfreq - paramsfreq))
    elif paramsfreq - rootfreq:
        logger.info("More parameter frequencies used than listed in root "
                    "attribute frequencies. Frequency not listed: %s",
                    list(paramsfreq - rootfreq))


def is_reliable_frame_counter(hdf):
    """returns if the parameter 'Frame Counter' is reliable."""
    try:
        pfc = hdf['Frame Counter']
    except KeyError:
        return False
    if np.ma.masked_inside(pfc, 0, 4095).count() != 0:
        return False
    # from split_hdf_to_segments.py
    fc_diff = np.ma.diff(pfc.array)
    fc_diff = np.ma.masked_equal(fc_diff, 1)
    fc_diff = np.ma.masked_equal(fc_diff, -4095)
    if fc_diff.count() == 0:
        return True
    return False


def validate_reliable_frame_counter_attribute(hdf):
    """
    Check if the root attribute reliable_frame_counter exists (It is required)
    and report the value and if the value is correctly set.
    """
    logger.info("Checking Root Attribute: 'reliable_frame_counter'")
    parameter_exists = 'Frame Counter' in hdf.keys()
    reliable = is_reliable_frame_counter(hdf)
    attribute_value = hdf.reliable_frame_counter
    correct_type = isinstance(hdf.reliable_frame_counter, bool)
    if attribute_value is None:
        logger.error("'reliable_frame_counter': Attribute not present "
                     "and is required.")
    else:
        logger.info("'reliable_frame_counter': Attribute is present.")
        if parameter_exists and reliable and attribute_value:
            logger.info("'Frame Counter' parameter is present and is "
                        "reliable and 'reliable_frame_counter' marked "
                        "as reliable.")
        elif parameter_exists and reliable and not attribute_value:
            logger.info("'Frame Counter' parameter is present and is "
                        "reliable, but 'reliable_frame_counter' not "
                        "marked as reliable.")
        elif parameter_exists and not reliable and attribute_value:
            logger.info("'Frame Counter' parameter is present and not "
                        "reliable, but 'reliable_frame_counter' is marked "
                        "as reliable.")
        elif parameter_exists and not reliable and not attribute_value:
            logger.info("'Frame Counter' parameter is present and not "
                        "reliable and 'reliable_frame_counter' not marked "
                        "as reliable.")
        elif not parameter_exists and attribute_value:
            logger.info("'Frame Counter' parameter not present, but "
                        "'reliable_frame_counter' marked as reliable. "
                        "Value should be False.")
        elif not parameter_exists and not attribute_value:
            logger.info("'Frame Counter' parameter not present and "
                        "'reliable_frame_counter' correctly set to False.")
        if not correct_type:
            logger.error("'reliable_frame_counter': Attribute is not a "
                         "Boolean type. Type is %s",
                         type(hdf.reliable_frame_counter).__name__)


def is_reliable_subframe_counter(hdf):
    """returns if the parameter 'Subframe Counter' is reliable."""
    try:
        sfc = hdf['Subframe Counter']
    except KeyError:
        return False
    sfc_diff = np.ma.masked_equal(np.ma.diff(sfc.array), 1)
    if sfc_diff.count() < len(sfc.array) / 4095:
        return True
    return False


def validate_reliable_subframe_counter_attribute(hdf):
    """
    Check if the root attribute reliable_subframe_counter exists
    (It is required) and report the value and if the value is correctly set.
    """
    logger.info("Checking Root Attribute: 'reliable_subframe_counter'")
    parameter_exists = 'Subframe Counter' in hdf.keys()
    reliable = is_reliable_subframe_counter(hdf)
    attribute_value = hdf.reliable_subframe_counter
    correct_type = isinstance(hdf.reliable_subframe_counter, bool)
    if attribute_value is None:
        logger.error("'reliable_subframe_counter': Attribute not present "
                     "and is required.")
    else:
        logger.info("'reliable_subframe_counter': Attribute is present.")
        if parameter_exists and reliable and attribute_value:
            logger.info("'Frame Counter' parameter is present and is "
                        "reliable and 'reliable_subframe_counter' marked "
                        "as reliable.")
        elif parameter_exists and reliable and not attribute_value:
            logger.info("'Frame Counter' parameter is present and is "
                        "reliable, but 'reliable_subframe_counter' not "
                        "marked as reliable.")
        elif parameter_exists and not reliable and attribute_value:
            logger.info("'Frame Counter' parameter is present and not "
                        "reliable, but 'reliable_subframe_counter' is "
                        "marked as reliable.")
        elif parameter_exists and not reliable and not attribute_value:
            logger.info("'Frame Counter' parameter is present and not "
                        "reliable and 'reliable_subframe_counter' not "
                        "marked as reliable.")
        elif not parameter_exists and attribute_value:
            logger.info("'Frame Counter' parameter not present, but "
                        "'reliable_subframe_counter' marked as reliable. "
                        "Value should be False.")
        elif not parameter_exists and not attribute_value:
            logger.info("'Frame Counter' parameter not present and "
                        "'reliable_subframe_counter' correctly set to False.")
        if not correct_type:
            logger.error("'reliable_subframe_counter': Attribute is not a "
                         "Boolean type. Type is %s",
                         type(hdf.reliable_subframe_counter).__name__)


def validate_start_timestamp_attribute(hdf):
    """
    Check if the root attribute start_timestamp exists
    and report the value.
    """
    logger.info("Checking Root Attribute: start_timestamp")
    if hdf.start_datetime:
        logger.info("'start_timestamp' attribute present.")
        logger.info("Time reported to be, %s", hdf.start_datetime)
        logger.info("Epoch timestamp value is: %s",
                    hdf.file.attrs['start_timestamp'])
        if 'float' in type(hdf.file.attrs['start_timestamp']).__name__:
            logger.info("'start_timestamp': Attribute is a float.")
        else:
            logger.error("'start_timestamp': Attribute is not a float. Type "
                         "reported as '%s'.",
                         type(hdf.file.attrs['start_timestamp']).__name__)
    else:
        logger.info("'start_timestamp': Attribute not present and is "
                    "optional.")


def validate_superframe_present_attribute(hdf):
    """
    Check if the root attribute superframe_present exists
    and report.
    """
    logger.info("Checking Root Attribute: superframe_present.")
    if hdf.superframe_present:
        logger.info("'superframe_present': Attribute present.")
    else:
        logger.info("'superframe_present': Attribute is not present and "
                    "is optional.")


def validate_file(hdffile, helicopter=False, names=None, states=False):
    """
    Attempts to open the HDF5 file in using FlightDataAccessor and run all the
    validation tests. If the file cannot be opened, it will attempt to open
    the file using the h5py package and validate the namespace to test the
    HDF5 group structure.
    """
    filename = hdffile.split(os.sep)[-1]
    open_with_h5py = False
    hdf = None
    logger.info("Verifying file '%s' with FlightDataAccessor.", filename)
    try:
        hdf = fda.open(hdffile, read_only=True)
    except Exception as err:
        logger.error("FlightDataAccessor cannot open '%s'. "
                     "Exception(%s: %s)", filename, type(err).__name__, err)
        open_with_h5py = True
    # If FlightDataAccessor errors upon opening it maybe because '/series'
    # is not included in the file. hdf_open attempts to create and fails
    # because we opening it as readonly. Verify the group structure by
    # using H5PY
    if open_with_h5py:
        logger.info("Checking that H5PY package can read the file.")
        try:
            hdf_alt = h5py.File(hdffile, 'r')
        except Exception as err:
            logger.error("Cannot open '%s' using H5PY. Exception(%s: %s)",
                         filename, type(err).__name__, err)
            return
        logger.info("File %s can be opened by H5PY, suggesting the format "
                    "is not compatible for POLARIS to use.",
                    filename)
        logger.info("Will just verify the HDF5 structure and exit.")
        validate_namespace(hdf_alt)
        hdf_alt.close()
    else:
        validate_namespace(hdf.file)
        # continue testing using FlightDataAccessor
        validate_root_attribute(hdf)
        validate_parameters(hdf, helicopter, names=names, states=states)
    if hdf:
        hdf.close()


def main():
    """Main"""
    parser = argparse.ArgumentParser(
        description="Flight Data Services, HDF5 Validator for POLARIS "
                    "compatibility.")

    parser.add_argument('--version', action='version', version='0.1.6')
    parser.add_argument(
        '--helicopter',
        help='Validates HDF5 file against helicopter core parameters.',
        action='store_true'
    )
    parser.add_argument(
        '-p', '--parameter',
        help='Validate a subset of parameters.',
        default=None,
        action='append'
    )
    parser.add_argument('--states', action='store_true',
                        help='Check parameter states are consistent')
    parser.add_argument(
        "-s",
        "--stop-on-error",
        help="Stop validation on the first error encountered.",
        action="store_true"
    )
    parser.add_argument(
        "-e",
        "--show-only-errors",
        help="Display only errors on screen.",
        action="store_true"
    )
    parser.add_argument(
        '-o',
        '--output',
        metavar='LOG',
        type=str,
        help='Saves all the screen messages, during validation to a log file.',
    )
    parser.add_argument(
        '-l',
        '--log-level',
        metavar='LEVEL',
        choices=['DEBUG', 'INFO', 'WARN', 'WARNING', 'ERROR'],
        help='Set logging level. [DEBUG|INFO|WARN|ERROR]',
    )
    parser.add_argument(
        'HDF5',
        help="The HDF5 file to be tested for POLARIS compatibility. "
        "This will also compressed hdf5 files with the extension '.gz'.)"
    )
    args = parser.parse_args()

    # Setup logger
    fmtr = logging.Formatter(r'%(levelname)-9s: %(message)s')

    log_lvl = None
    if args.log_level:
        lvl = logging.getLevelName(args.log_level)
        log_lvl = lvl if isinstance(lvl, int) else None

    # setup a output file handler, if required
    if args.output:
        logfilename = args.output
        file_hdlr = logging.FileHandler(logfilename, mode='w')
        if log_lvl:
            file_hdlr.setLevel(log_lvl)
        else:
            file_hdlr.setLevel(logging.DEBUG)
        file_hdlr.setFormatter(fmtr)
        logger.addHandler(file_hdlr)

    # setup a stream handler (display to terminal)
    term_hdlr = logging.StreamHandler()
    if args.show_only_errors:
        term_hdlr.setLevel(logging.ERROR)
    else:
        if log_lvl:
            term_hdlr.setLevel(log_lvl)
        else:
            term_hdlr.setLevel(logging.INFO)
    term_hdlr.setFormatter(fmtr)
    logger.addHandler(term_hdlr)

    # setup a separate handler so we count log levels and stop on first error
    hdfv_hdlr = HDFValidatorHandler(args.stop_on_error)
    error_count = hdfv_hdlr.get_error_counts()
    hdfv_hdlr.setLevel(logging.INFO)
    hdfv_hdlr.setFormatter(fmtr)
    logger.addHandler(hdfv_hdlr)

    logger.setLevel(logging.DEBUG)
    logger.debug("Arguments: %s", str(args))
    try:
        validate_file(args.HDF5, args.helicopter, names=args.parameter, states=args.states)
    except StoppedOnFirstError:
        msg = "First error encountered. Stopping as requested."
        logger.info(msg)
        if args.show_only_errors:
            print(msg)

    for hdr in logger.handlers:
        if isinstance(hdr, HDFValidatorHandler):
            error_count = hdr.get_error_counts()

    log_title("Results")
    msg = "Validation ended with, %s errors and %s warnings" %\
        (error_count['errors'], error_count['warnings'])
    logger.info(msg)
    if args.show_only_errors:
        print(msg)

if __name__ == '__main__':
    main()
