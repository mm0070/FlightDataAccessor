#!/usr/bin/env python
'''
A script to generate python lists from list_data/parameters-*.txt files into a
python module, allowing lists to be imported by other scripts.
'''
from __future__ import print_function

import os
import glob

FILES = glob.glob(os.path.join('list_data', 'parameters-*.txt'))

GEN_FILENAME = 'parameter_lists.py'


def variable_from_filename(filename):
    '''
    Make a varible name from the filename string.
    '''
    return os.path.splitext(
        filename)[0].split(os.sep)[-1].upper().replace('-', '_')


def generate_parameter_list():
    '''
    Generate a python file and format the information read from the parameter
    text files into the python lists.
    '''
    newpy = open(GEN_FILENAME, 'w')

    newpy.write("'''\n%s is auto generated by %s and is compiled from:\n    %s"
                "\n'''\n\n" % (GEN_FILENAME, __file__.split(os.sep)[-1],
                               "\n    ".join(FILES)))
    for txtfile in FILES:
        varname = variable_from_filename(txtfile)
        newpy.write('# Parameters from %s\n' % txtfile)
        newpy.write('%s = [\n' % varname)
        with open(txtfile, 'r') as fhdl:
            newpy.writelines("    '%s',\n" % l.strip() for l in fhdl)
        newpy.write(']\n\n')

    liststr = ' +\n    '.join(variable_from_filename(f) for f in FILES)
    newpy.write('# List of all parameters from all files with duplicates '
                'removed.\n')
    newpy.write("PARAMETERS_FROM_FILES = list(set(\n    %s\n))\n" % liststr)
    newpy.close()


def main():
    '''
    Main
    '''
    if not FILES:
        print("No files matching paramters-*.txt to generate %s"
              % (GEN_FILENAME))
        return
    print("Will generate %s from the following sources:\n%s and %s"
          % (GEN_FILENAME, ", ".join(FILES[:-1]), FILES[-1]))
    if os.path.isfile(GEN_FILENAME):
        response = raw_input("%s already exists. Continuing will overwrite "
                             "this file, continue? [Y/n]:" % GEN_FILENAME)
        if response not in ['Y', 'y', '']:
            print("Exiting, no changes made.")
            return
    print("Creating %s..." % GEN_FILENAME)
    generate_parameter_list()
    print("Finished.")

if __name__ == '__main__':
    main()
