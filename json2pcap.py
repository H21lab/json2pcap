#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# Copyright 2020, Martin Kacer <kacer.martin[AT]gmail.com>
#
# Wireshark - Network traffic analyzer
# By Gerald Combs <gerald@wireshark.org>
# Copyright 1998 Gerald Combs
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import sys
import ijson
import operator
import copy
import os
import binascii
import array
import argparse
import subprocess
from collections import OrderedDict
from scapy import all as scapy

try:
    # Python 2 forward compatibility
    range = xrange
except NameError:
    pass

def make_unique(key, dct):
    counter = 0
    unique_key = key

    while unique_key in dct:
        counter += 1
        unique_key = '{}_{}'.format(key, counter)
    return unique_key


def parse_object_pairs(pairs):
    dct = OrderedDict()
    for key, value in pairs:
        if key in dct:
            key = make_unique(key, dct)
        dct[key] = value

    return dct

#
# ********* PY TEMPLATES *********
#
def read_py_function(name):
    s = ''
    record = False
    indent = 0

    file = open(__file__)
    for line in file:

        ind = len(line) - len(line.lstrip())

        if (line.find("def " + name) != -1):
            record = True
            indent = ind
        elif (record == True and indent == ind and len(line) > 1):
            record = False

        if (record == True):
            s = s + line

    file.close()
    return s

py_header = """#!/usr/bin/env python
# -*- coding: utf-8 -*-

# File generated by json2pcap.py
# json2pcap.py created by Martin Kacer, 2020

import os
import binascii
import array
import sys
import subprocess
from collections import OrderedDict

# *****************************************************
# *     PACKET PAYLOAD GENERATED FROM INPUT PCAP      *
# *     Modify this function to edit the packet       *
# *****************************************************
def main():
    d = OrderedDict()
"""

py_footer = """    generate_pcap(d)

# *****************************************************
# *             FUNCTIONS from TEMPLATE               *
# *    Do not edit these functions if not required    *
# *****************************************************

"""
py_footer = py_footer + read_py_function("to_pcap_file")
py_footer = py_footer + read_py_function("hex_to_txt")
py_footer = py_footer + read_py_function("to_bytes")
py_footer = py_footer + read_py_function("lsb")
py_footer = py_footer + read_py_function("rewrite_frame")
py_footer = py_footer + read_py_function("assemble_frame")
py_footer = py_footer + read_py_function("generate_pcap")

py_footer = py_footer + """

if __name__ == '__main__':
    main()
"""
#
# ***** End of PY TEMPLATES ******
#



#
# ********** FUNCTIONS ***********
#

def raw_flat_collector(dict):
    if hasattr(dict, 'items'):
        for k, v in dict.items():
            if k.endswith("_raw"):
                yield k, v
            else:
                for val in raw_flat_collector(v):
                    yield val


# d - input dictionary, parsed from json
# r - result dictionary
# frame_name - parent protocol name
# frame_position - parent protocol position
def py_generator(d, r, frame_name='frame_raw', frame_position=0):
    if (d is None or d is None):
        return

    if hasattr(d, 'items'):
        for k, v in d.items():

            # no recursion
            if ( k.endswith("_raw") or ("_raw_" in k) ):
                if (isinstance(v[1], (list, tuple)) or isinstance(v[2], (list, tuple)) ):
                    #i = 1;
                    for _v in v:
                        h = _v[0]
                        p = _v[1]
                        l = _v[2] * 2
                        b = _v[3]
                        t = _v[4]
                        if (len(h) != l):
                            l = len(h)

                        p = p - frame_position

                        # Add into result dictionary
                        key = str(k).replace('.', '_')
                        key = make_unique(key, r)

                        fn = frame_name.replace('.', '_')
                        if (fn == key):
                            fn = None
                        value = [fn , h, p, l, b, t]

                        r[key] = value

                else:
                    h = v[0]
                    p = v[1]
                    l = v[2] * 2
                    b = v[3]
                    t = v[4]
                    if (len(h) != l):
                        l = len(h)

                    p = p - frame_position

                    # Add into result dictionary
                    key = str(k).replace('.', '_')
                    key = make_unique(key, r)

                    fn = frame_name.replace('.', '_')
                    if (fn == key):
                        fn = None
                    value = [fn , h, p, l, b, t]

                    r[key] = value

            # recursion
            else:
                if isinstance(v, dict):
                    fn = frame_name
                    fp = frame_position

                    # if there is also preceding raw protocol frame use it
                    # remove tree suffix
                    key = k
                    if (key.endswith("_tree") or ("_tree_" in key)):
                        key = key.replace('_tree', '')

                    raw_key = key + "_raw"
                    if (raw_key in d):
                        # f =  d[raw_key][0]
                        fn = raw_key
                        fp = d[raw_key][1]


                    py_generator(v, r, fn, fp)

                elif isinstance(v, (list, tuple)):

                    fn = frame_name
                    fp = frame_position

                    # if there is also preceding raw protocol frame use it
                    # remove tree suffix
                    key = k
                    if (key.endswith("_tree") or ("_tree_" in key)):
                        key = key.replace('_tree', '')

                    raw_key = key + "_raw"
                    if (raw_key in d):
                        fn = raw_key
                        fp = d[raw_key][1]

                    for _v in v:
                        py_generator(_v, r, frame_name, frame_position)




# To emulate Python 3.2
def to_bytes(n, length, endianess='big'):
    h = '%x' % n
    s = bytearray.fromhex(('0' * (len(h) % 2) + h).zfill(length * 2))
    return s if endianess == 'big' else s[::-1]

# Returns the index, counting from 0, of the least significant set bit in x
def lsb(x):
    return (x & -x).bit_length() - 1

# Replace parts of original_string by new_string, only if mask in the byte is not ff
def multiply_strings(original_string, new_string, mask):

    ret_string = new_string
    if mask == None:
        return ret_string
    for i in range(0, min(len(original_string), len(new_string), len(mask)), 2):
        if mask[i:i + 2] == 'ff':
            #print("ff")
            ret_string = ret_string[:i] + original_string[i:i + 2] + ret_string[i + 2:]

    return ret_string

# Rewrite frame
# h - hex bytes
# p - position
# l - length
# b - bitmask
# t - type
# frame_amask - optional, anonymization mask (00 - not anonymized byte, ff - anonymized byte)
def rewrite_frame(frame_raw, h, p, l, b, t, frame_amask = None):
    # no bitmask
    if(b == 0):
        if (len(h) != l):
            l = len(h)
        frame_raw_new = frame_raw[:p] + h + frame_raw[p + l:]
        return multiply_strings(frame_raw, frame_raw_new, frame_amask)
    # bitmask
    else:
        # get hex string from frame which will be replaced
        _h = frame_raw[p:p + l]

        # add 0 padding to have correct length
        if (len(_h) % 2 == 1):
            _h = '0' + _h
        if (len(h) % 2 == 1):
            h = '0' + h

        # Only replace bits defined by mask
        # new_hex = (old_hex & !mask) | (new_hex & mask)
        _H = bytearray.fromhex(_h)
        _H = array.array('B', _H)

        M = to_bytes(b, len(_H))
        M = array.array('B', M)
        # shift mask aligned to position
        for i in range(len(M)):
            if (i + p / 2) < len(M):
                M[i] = M[i + int(p / 2)]
            else:
                M[i] = 0x00

        H = bytearray.fromhex(h)
        H = array.array('B', H)

        # for i in range(len(_H)):
        #    print "{0:08b}".format(_H[i]),
        # print
        # for i in range(len(M)):
        #    print "{0:08b}".format(M[i]),
        # print

        j = 0;
        for i in range(len(_H)):
            if (M[i] != 0):
                v = H[j] << lsb(M[i])
                # print "Debug: {0:08b}".format(v),
                _H[i] = (_H[i] & ~M[i]) | (v & M[i])
                # print "Debug: " + str(_H[i]),
                j = j + 1;

        # for i in range(len(_H)):
        #    print "{0:08b}".format(_H[i]),
        # print

        masked_h = binascii.hexlify(_H)
        masked_h = masked_h.decode('ascii')

        frame_raw_new = frame_raw[:p] + str(masked_h) + frame_raw[p + l:]
        return multiply_strings(frame_raw, frame_raw_new, frame_amask)


def assemble_frame(d, frame_time):
    input = d['frame_raw'][1]
    isFlat = False
    linux_cooked_header = False;
    while(isFlat == False):
        isFlat = True
        for key, val in d.items():
            h = str(val[1])     # hex
            p = val[2] * 2      # position
            l = val[3] * 2      # length
            b = val[4]          # bitmask
            t = val[5]          # type

            if (key == "sll_raw"):
                linux_cooked_header = True;

            # only if the node is not parent
            isParent = False
            for k, v in d.items():
                if (v[0] == key):
                    isParent = True
                    isFlat = False
                    break

            if (isParent == False and val[0] is not None):
                d[val[0]][1] = rewrite_frame(d[val[0]][1], h, p, l, b, t)
                del d[key]

    output = d['frame_raw'][1]

    # for Linux cooked header replace dest MAC and remove two bytes to reconstruct normal frame
    if (linux_cooked_header):
        output = "000000000000" + output[6*2:] # replce dest MAC
        output = output[:12*2] + "" + output[14*2:] # remove two bytes before Protocol

    return output

#
# ************ MAIN **************
#
parser = argparse.ArgumentParser(description="""
Utility to generate pcap from json format.

Packet modification:
In input json  it is possible to  modify the raw values  of decoded fields.
The  output  pcap  will  include  the modified  values.  The  algorithm  of
generating the output pcap is to get all raw hex fields from input json and
then  assembling them  by layering  from longest  (less decoded  fields) to
shortest  (more decoded  fields). It  means if  the modified  raw field  is
shorter field (more decoded field) it takes precedence against modification
in longer field  (less decoded field). If the json  includes duplicated raw
fields with  same position and  length, the behavior is  not deterministic.
For manual packet editing it is  always possible to remove any not required
raw fields from json, only frame_raw is field mandatory for reconstruction.

Packet modification with -p switch:
The python  script is generated  instead of  pcap. This python  script when
executed  will  generate the  pcap  of  1st  packet  from input  json.  The
generated code includes the decoded fields and the function to assembly the
packet.  This enables  to modify  the script  and programmatically  edit or
encode the packet variables. The assembling algorithm is different, because
the decoded packet fields are relative and points to parent node with their
position (compared to input json which has absolute positions).

Pcap anonymization with -a switch:
The script allows to  anonymize the selected json raw  fields. If the fields
selected for anonymization are located on lower protocol layers, then are not
overwritten  by  upper  fields  which  are  not  marked  for  anonymization.
The pcap anonymization can be performed in the following way:

tshark -r original.pcap -T json -x | \\
python json2pcap.py -a "ip.src_raw" -a "ip.dst_raw" -o anonymized.pcap


""", formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument('-i', '--infile', nargs='?', help='json generated by tshark -T json -x\nor by tshark -T jsonraw (not preserving frame timestamps).\nIf no inpout file is specified script reads from stdin.')
parser.add_argument('-o', '--outfile', required=True, help='output pcap filename')
parser.add_argument('-p', '--python', help='generate python payload instead of pcap (only 1st packet)', default=False, action='store_true')
parser.add_argument('-a', '--anonymize', help='anonymize the specific raw field (e.g. -a "ip.src_raw" -a "ip.dst_raw")', action='append', metavar='ANONYMIZED_FIELD')
parser.add_argument('-v', '--verbose', help='verbose output', default=False, action='store_true')
args = parser.parse_args()

# read JSON
infile = args.infile
outfile = args.outfile

# Read from input file
if infile:
    data_file = open(infile)
# Read from pipe
else:
    data_file = sys.stdin

input_frame_raw = ''
frame_raw = ''

# Generate pcap
if args.python == False:
    pcap_out = scapy.PcapWriter(outfile, append=False, sync=False)

    # Iterate over packets in JSON
    for packet in ijson.items(data_file, "item", buf_size=200000):
        _list = []
        linux_cooked_header = False;

        # get flat raw fields into _list
        for raw in raw_flat_collector(packet['_source']['layers']):
            if (raw[0] == "frame_raw"):
                frame_raw = raw[1][0]
                frame_amask = "0"*len(frame_raw) # initialize anonymization mask
                input_frame_raw = copy.copy(frame_raw)
                frame_time = None
                if 'frame.time_epoch' in packet['_source']['layers']['frame']:
                    frame_time = packet['_source']['layers']['frame']['frame.time_epoch']
            else:
                # add into value list into raw[5] the field name
                raw[1].append(raw[0])
                _list.append(raw[1])
            if (raw[0] == "sll_raw"):
                linux_cooked_header = True

        # sort _list
        sorted_list = sorted(_list, key=operator.itemgetter(1), reverse=False)
        sorted_list = sorted(sorted_list, key=operator.itemgetter(2), reverse=True)
        # print("Debug: " + str(sorted_list))

        # rewrite frame
        for raw in sorted_list:
            if (len(raw) >= 6):
                h = str(raw[0])  # hex
                p = raw[1] * 2  # position
                l = raw[2] * 2  # length
                b = raw[3]  # bitmask
                t = raw[4]  # type
                # raw[5]    # field_name (added by script)

                # anonymize fields
                if (args.anonymize and raw[5] in args.anonymize):
                    h = 'f' * len(h)

                if (isinstance(p, (list, tuple)) or isinstance(l, (list, tuple))):
                    for r in raw:
                        _h = str(r[0])  # hex
                        _p = r[1] * 2  # position
                        _l = r[2] * 2  # length
                        _b = r[3]  # bitmask
                        _t = r[4]  # type
                        # raw[5]    # field_name (added by script)

                        # anonymize fields
                        if (args.anonymize and raw[5] in args.anonymize):
                            _h = 'f' * len(_h)

                        # print("Debug: " + str(raw))
                        frame_raw = rewrite_frame(frame_raw, _h, _p, _l, _b, _t, frame_amask)

                        # update anonymization mask
                        if (args.anonymize and raw[5] in args.anonymize):
                            frame_amask = rewrite_frame(frame_amask, _h, _p, _l, _b, _t)

                else:
                    # print("Debug: " + str(raw))
                    frame_raw = rewrite_frame(frame_raw, h, p, l, b, t, frame_amask)

                    # update anonymization mask
                    if (args.anonymize and raw[5] in args.anonymize):
                        frame_amask = rewrite_frame(frame_amask, h, p, l, b, t)

        # for Linux cooked header replace dest MAC and remove two bytes to reconstruct normal frame using text2pcap
        if (linux_cooked_header):
           frame_raw = "000000000000" + frame_raw[6 * 2:]  # replce dest MAC
           frame_raw = frame_raw[:12 * 2] + "" + frame_raw[14 * 2:]  # remove two bytes before Protocol

        # Testing: remove comment to compare input and output for not modified json
        if (args.verbose and input_frame_raw != frame_raw):
            print("Modified frames: ")
            s1 = input_frame_raw
            s2 = frame_raw
            print(s1)
            print(s2)
            if (len(s1) == len(s2)):
                d = [i for i in range(len(s1)) if s1[i] != s2[i]]
                print(d)

        new_packet = scapy.Packet(bytearray.fromhex(frame_raw))
        new_packet.time = float(frame_time)
        pcap_out.write(new_packet)

# Generate python payload only for first packet
else:
    py_outfile = outfile + '.py'
    f = open(py_outfile, 'w')

    #for packet in json:
    for packet in ijson.items(data_file, "item", buf_size=200000):
        f.write(py_header)

        r = OrderedDict({})

        #print "packet = " + str(packet['_source']['layers'])
        py_generator(packet['_source']['layers'], r)

        for key, value in r.items() :
            f.write("    d['" + key + "'] =",)
            f.write(" " + str(value) + "\n")

        f.write(py_footer)

        # Currently only first packet is used from pcap
        f.close

        print("Generated " + py_outfile)

        break
