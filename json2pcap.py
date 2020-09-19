#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# Copyright 2020, Martin Kacer <kacer.martin[AT]gmail.com> and contributors
#
# Wireshark - Network traffic analyzer
# By Gerald Combs <gerald@wireshark.org>
# Copyright 1998 Gerald Combs
#
# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import ijson
import operator
import copy
import os
import binascii
import array
import argparse
import subprocess
import string
import random
import math
import hashlib
import re
from collections import OrderedDict
from scapy import all as scapy
import bitstring

try:
    # Python 2 forward compatibility
    range = xrange
except NameError:
    pass

# Field anonymization class
class AnonymizedField:
    '''
    The Anonymization field object specifying anonymization
    :filed arg: field name
    :type arg: anonymization type [0 masking 0xff, 1 anonymization shake_256]
    :start arg: If specified, the anonymization starts at given byte number
    :end arg: If specified, the anonymization ends at given byte number
    '''
    def __init__(self, field, type):
        self.field = field
        self.type = type
        self.start = None
        self.end = None

        match = re.search(r'(\S+)\[(-?\d+)?:(-?\d+)?\]', field)
        if match:
            self.field = match.group(1)
            self.start = match.group(2)
            if self.start is not None:
                self.start = int(self.start)
            self.end = match.group(3)
            if self.end is not None:
                self.end = int(self.end)

    # Returns the new field value after anonymization
    def anonymize_field_shake256(self, field, type, salt):
        shake = hashlib.shake_256(str(field + ':' + salt).encode('utf-8'))

        # String type, output should be ASCII
        if type in [26, 27, 28]:
            length = math.ceil(len(field)/4)
            shake_hash = shake.hexdigest(length)
            ret_string = array.array('B', str.encode(shake_hash))
            ret_string = ''.join('{:02x}'.format(x) for x in ret_string)
        # Other types, output could be HEX
        else:
            length = math.ceil(len(field)/2)
            shake_hash = shake.hexdigest(length)
            ret_string = shake_hash

        # Correct the string length
        if (len(ret_string) < len(field)):
            ret_string = ret_string.ljust(len(field))
        if (len(ret_string) > len(field)):
            ret_string = ret_string[:len(field)]

        return ret_string

    def anonymize_field(self, _h, _t, salt):
        s = 0
        e = None
        if self.start:
            s = self.start
        if self.end:
            e = self.end
            if e < 0:
                e = len(_h) + e
        else:
            e = len(_h)
        h = _h[s:e]
        if self.type == 0:
            h = 'f' * len(h)
        elif self.type == 1:
            h = self.anonymize_field_shake256(h, _t, salt)

        h_mask = '0' * len(_h[0:s]) + 'f' * len(h) + '0' * len(_h[e:])
        h = _h[0:s] + h + _h[e:]
        return [h, h_mask]

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
from scapy import all as scapy

try:
    # Python 2 forward compatibility
    range = xrange
except NameError:
    pass

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
py_footer = py_footer + read_py_function("to_bytes")
py_footer = py_footer + read_py_function("lsb")
py_footer = py_footer + read_py_function("multiply_strings")
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
                # check if the _raw value is nested list
                if any(isinstance(i, list) for i in v):
                    for _v in v:
                        yield k, _v
                # else
                else:
                    yield k, v
            else:
                # check if the non _raw value is list
                if type(v) is list:
                    for _v in v:
                        for val in raw_flat_collector(_v):
                            yield val
                # else
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
    s = bytearray.fromhex(('0' * (len(h) % 2) + h).zfill(length * 2)[:length * 2])
    return s if endianess == 'big' else s[::-1]

# Returns the index, counting from 0, of the least significant set bit in x
def lsb(x):
    return (x & -x).bit_length() - 1

# Returns the index, counting from 0, of the least significant set bit in x from bytetarray
def lsb_bytearray(X):
    r = 0
    for x in reversed(X):
        if (lsb(x) != -1):
            return r + lsb(x)
        else:
            r = r + 8
    return -1

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

    #print("frame_raw = " + str(frame_raw))
    #print("h = " + str(h))
    #print("p = " + str(p))
    #print("l = " + str(l))
    #print("b = " + str(b))
    #print("t = " + str(t))
    #print("frame_amask = " + str(frame_amask))

    if p < 0 or l <= 0 or h is None or not h:
        return frame_raw

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
        #print("_h = " + _h)

        # add 0 padding to have correct length
        if (len(_h) % 2 == 1):
            _h = '0' + _h
        if (len(h) % 2 == 1):
            h = '0' + h

        # Only replace bits defined by mask
        # new_hex = (old_hex & !mask) | (new_hex & mask)
        _H = bytearray.fromhex(_h)
        _H = (array.array('B', _H))
        #b_H = bitstring.BitArray("0x" + _h)

        # for certain types reverse byte array
        REVERSED_BYTE_ORDER_TYPES = []
        if(t in REVERSED_BYTE_ORDER_TYPES):
            _H = _H[::-1]
        b_H = bitstring.BitArray(_H)

        # reset the mask byte array
        M = to_bytes(b, len(_H))
        M = array.array('B', M)
        bM = bitstring.BitArray(M)

        # shift mask aligned to position
        #for i in range(len(M)):
        #    if (i + p / 2) < len(M):
        #        M[i] = M[i + int(p / 2)]
        #    else:
        #        M[i] = 0x00

        #for i in range(len(_H)):
        #    print("_H = {0:08b}".format(_H[i]))
        #for i in range(len(M)):
        #    print(" M = {0:08b}".format(M[i]))

        # increase the array if needed
        if len(h) < len(M)*2:
            h = h.zfill(len(M)*2)
        #if len(h) < len(M)*2:
        #    h = h[::-1].zfill(len(M)*2)[::-1]

        H = bytearray.fromhex(h)
        H = array.array('B', H)
        bH = bitstring.BitArray("0x" + h)

        #print("bM = " + str(bM.bin))
        # bit shift the H to the left by bitmask, increase h if the mask is larger
        if lsb_bytearray(M) != -1:
            bH = bH << int(lsb_bytearray(M))


        #for i in range(len(H)):
        #    print("after  shift H = {0:08b}".format(_H[i]))

        #for i in range(len(H)):
        #    print(" H = {0:08b}".format(H[i]))

        #j = 0;
        #for i in range(len(_H)):
            #if (M[i] != 0):
            #v = H[j] << lsb(M[i])
            #print("Debug: {0:08b}".format(v))
        #    _H[i] = (H[i] & M[i]) | (_H[i] & ~M[i]) #| (v & M[i])
            #print("Debug: " + str(_H[i]))
        #    j = j + 1;

        #print("================")
        #print("b_H = " + str(b_H.bin))
        #print("bM = " + str(bM.bin))
        #print("bH = " + str(bH.bin))
        b_H = bH & bM | b_H & ~bM
        #print("b_H = " + str(b_H.bin))
        #print("================")
        _H = b_H.tobytes()

        #for i in range(len(_H)):
        #    print("_H = {0:08b}".format(_H[i]))

        # for certain types reverse byte array
        if(t in REVERSED_BYTE_ORDER_TYPES):
            _H = _H[::-1]
        masked_h = binascii.hexlify(_H[::-1])
        masked_h = masked_h.decode('ascii')

        frame_raw_new = frame_raw[:p] + str(masked_h) + frame_raw[p + l:]

        return multiply_strings(frame_raw, frame_raw_new, frame_amask)


def assemble_frame(d, frame_time):
    input = d['frame_raw'][1]
    isFlat = False
    linux_cooked_header = False;
    while(isFlat == False):
        isFlat = True
        _d = d.copy()
        for key, val in _d.items():
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

def generate_pcap(d):
    # 1. Assemble frame
    input = d['frame_raw'][1]
    output = assemble_frame(d, None)
    print(input)
    print(output)
    # 2. Testing: compare input and output for not modified json
    if (input != output):
        print("Modified frames: ")
        s1 = input
        s2 = output
        print(s1)
        print(s2)
        if (len(s1) == len(s2)):
            d = [i for i in range(len(s1)) if s1[i] != s2[i]]
            print(d)
    # 3. Generate pcap
    outfile = sys.argv[0] + ".pcap"
    pcap_out = scapy.PcapWriter(outfile, append=False, sync=False)
    new_packet = scapy.Packet(bytearray.fromhex(output))
    pcap_out.write(new_packet)
    print("Generated " + outfile)

#
# ************ MAIN **************
#
VERSION = "1.2"

parser = argparse.ArgumentParser(description="""
json2pcap {version}

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

Pcap masking and anonymization with -m and -a switch:
The script allows to mask or anonymize the selected json raw fields. If the
The fields are selected and located on  lower protocol layers, they are not
The overwritten by  upper fields  which are not  marked by  these switches.
The pcap masking and anonymization can be performed in the following way:

tshark -r orig.pcap -T json -x --no-duplicate-keys | \ python json2pcap.py
-m "ip.src_raw" -a "ip.dst_raw" -o anonymized.pcap
In this example the ip.src_raw field is masked with ffffffff by byte values
and ip.dst_raw is hashed by randomly generated salt.

Additionally the following syntax is valid to anonymize portion of field
tshark -r orig.pcap -T json -x --no-duplicate-keys  | \ python json2pcap.py
-m "ip.src_raw[2:]" -a "ip.dst_raw[:-2]" -o anonymized.pcap
Where the src_ip first byte is preserved and dst_ip last byte is preserved.
And the same can be achieved by
tshark -r orig.pcap -T json -x --no-duplicate-keys | \ python json2pcap.py
-m "ip.src_raw[2:8]" -a "ip.dst_raw[0:6]" -o anonymized.pcap

Masking and anonymization  limitations are mainly the following:
- In case  the tshark is performing reassembling from  multiple frames, the
backward pcap  reconstruction is not  properly performed and can  result in
malformed frames.
- The  new values  in the  fields could  violate the  field format,  as the
json2pcap  is  no performing  correct  protocol  encoding with  respect  to
allowed values of the target field and field encoding.

""".format(version=VERSION), formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument('--version', action='version', version='%(prog)s ' + VERSION)
parser.add_argument('-i', '--infile', nargs='?', help='json generated by tshark -T json -x\nor by tshark -T jsonraw (not preserving frame timestamps).\nIf no inpout file is specified script reads from stdin.')
parser.add_argument('-o', '--outfile', required=True, help='output pcap filename')
parser.add_argument('-p', '--python', help='generate python payload instead of pcap (only 1st packet)', default=False, action='store_true')
parser.add_argument('-m', '--mask', help='mask the specific raw field (e.g. -m "ip.src_raw" -m "ip.dst_raw[2:6]")', action='append', metavar='MASKED_FIELD')
parser.add_argument('-a', '--anonymize', help='anonymize the specific raw field (e.g. -a "ip.src_raw[2:]" -a "ip.dst_raw[:-2]")', action='append', metavar='ANONYMIZED_FIELD')
parser.add_argument('-s', '--salt', help='salt use for anonymization. If no value is provided it is randomized.', default=None)
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

# Parse anonymization fields
anonymize = {}
if args.mask:
    for m in args.mask:
        if not '_raw' in m:
            print("Error: The specified fields by -m switch should be raw fields. " + m + " does not have _raw suffix")
            sys.exit()
        af = AnonymizedField(m, 0)
        anonymize[af.field] = af
if args.anonymize:
    for a in args.anonymize:
        if not '_raw' in a:
            print("Error: The specified fields by -a switch should be raw fields. " + a + " does not have _raw suffix")
            sys.exit()
        af = AnonymizedField(a, 1)
        anonymize[af.field] = af

input_frame_raw = ''
frame_raw = ''
frame_time = None

salt = args.salt
if salt is None:
    # generate random salt if no salt was provided
    salt = ''.join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(10))

# Generate pcap
if args.python == False:
    pcap_out = scapy.PcapWriter(outfile, append=False, sync=False)

    # Iterate over packets in JSON
    for packet in ijson.items(data_file, "item", buf_size=200000):
        _list = []
        linux_cooked_header = False;

        # get flat raw fields into _list
        for raw in raw_flat_collector(packet['_source']['layers']):
            if len(raw) >= 2:
                if (raw[0] == "frame_raw"):
                    frame_raw = raw[1][0]
                    frame_amask = "0"*len(frame_raw) # initialize anonymization mask
                    input_frame_raw = copy.copy(frame_raw)
                    frame_time = None
                    if 'frame.time_epoch' in packet['_source']['layers']['frame']:
                        frame_time = packet['_source']['layers']['frame']['frame.time_epoch']
                else:
                    # add into value list into raw[5] the field name
                    if isinstance(raw[1], list):
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
            if len(raw) >= 6:
                h = str(raw[0])  # hex
                p = raw[1] * 2   # position
                l = raw[2] * 2   # length
                b = raw[3]       # bitmask
                t = raw[4]       # type
                # raw[5]         # field_name (added by script)
                h_mask = h       # hex for anonymization mask

                # anonymize fields
                if (raw[5] in anonymize):
                    [h, h_mask] = anonymize[raw[5]].anonymize_field(h, t, salt)

                if (isinstance(p, (list, tuple)) or isinstance(l, (list, tuple))):
                    for r in raw:
                        _h = str(r[0])  # hex
                        _p = r[1] * 2   # position
                        _l = r[2] * 2   # length
                        _b = r[3]       # bitmask
                        _t = r[4]       # type
                        # raw[5]        # field_name (added by script)
                        _h_mask = _h    # hex for anonymization mask

                        # anonymize fields
                        if (raw[5] in anonymize):
                            [_h, _h_mask]  = anonymize[raw[5]].anonymize_field(_h, _t, salt)

                        # print("Debug: " + str(raw))
                        frame_raw = rewrite_frame(frame_raw, _h, _p, _l, _b, _t, frame_amask)

                        # update anonymization mask
                        if (raw[5] in anonymize):
                            frame_amask = rewrite_frame(frame_amask, _h_mask, _p, _l, _b, _t)

                else:
                    #print("Debug: " + str(raw))
                    #print("Debug: " + str(frame_raw))
                    s1 = frame_raw
                    frame_raw = rewrite_frame(frame_raw, h, p, l, b, t, frame_amask)
                    s2 = frame_raw

                    #if (s1 != s2):
                    #    print("Modified fields: ")
                    #    print("Field: " + str(raw))
                    #    print("In : " + str(s1))
                    #    print("Out: " + str(s2))
                    #    d = [i for i in range(len(s1)) if s1[i] != s2[i]]
                    #    print(d)
                    #print("Debug: " + str(frame_raw))

                    # update anonymization mask
                    if (raw[5] in anonymize):
                        frame_amask = rewrite_frame(frame_amask, h_mask, p, l, b, t)

        # for Linux cooked header replace dest MAC and remove two bytes to reconstruct normal frame
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
        if frame_time:
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
