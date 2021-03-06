#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''
gnpy.core.equipment
===================

This module contains functionality for specifying equipment.
'''

from numpy import clip, polyval
from sys import exit
from operator import itemgetter
from math import isclose
from pathlib import Path
from json import loads
from gnpy.core.utils import lin2db, db2lin, load_json
from collections import namedtuple
from gnpy.core.elements import Edfa

Model_vg = namedtuple('Model_vg', 'nf1 nf2 delta_p')
Model_fg = namedtuple('Model_fg', 'nf0')
Fiber = namedtuple('Fiber', 'type_variety dispersion gamma')
Spans = namedtuple('Spans', 'power_mode delta_power_range_db max_length length_units \
                             max_loss padding EOL con_in con_out')
Transceiver = namedtuple('Transceiver', 'type_variety frequency mode')
Roadms = namedtuple('Roadms', 'gain_mode_default_loss power_mode_pref')
SI = namedtuple('SI', 'f_min f_max baud_rate spacing roll_off \
                       power_dbm power_range_db OSNR bit_rate')
AmpBase = namedtuple(
    'AmpBase',
    'type_variety type_def gain_flatmax gain_min p_max'
    ' nf_model nf_fit_coeff nf_ripple dgt gain_ripple out_voa_auto allowed_for_design')
class Amp(AmpBase):
    def __new__(cls,
            type_variety, type_def, gain_flatmax, gain_min, p_max, nf_model=None,
            nf_fit_coeff=None, nf_ripple=None, dgt=None, gain_ripple=None,
             out_voa_auto=False, allowed_for_design=True):
        return super().__new__(cls,
            type_variety, type_def, gain_flatmax, gain_min, p_max,
            nf_model, nf_fit_coeff, nf_ripple, dgt, gain_ripple,
            out_voa_auto, allowed_for_design)

    @classmethod
    def from_advanced_json(cls, filename, **kwargs):
        with open(filename) as f:
            json_data = loads(f.read())
        return cls(**{**kwargs, **json_data, 'type_def':None, 'nf_model':None})

    @classmethod
    def from_default_json(cls, filename, **kwargs):
        with open(filename) as f:
            json_data = loads(f.read())
        type_variety = kwargs['type_variety']
        type_def = kwargs.get('type_def', 'variable_gain') #default compatibility with older json eqpt files
        nf_def = None

        if type_def == 'fixed_gain':
            try:
                nf0 = kwargs.pop('nf0')
            except KeyError: #nf0 is expected for a fixed gain amp
                print(f'missing nf0 value input for amplifier: {type_variety} in eqpt_config.json')
                exit()
            try: #remove all remaining nf inputs
                del kwargs['nf_min']
                del kwargs['nf_max']
            except KeyError: pass #nf_min and nf_max are not needed for fixed gain amp
            nf_def = Model_fg(nf0)
        elif type_def == 'variable_gain':
            gain_min, gain_max = kwargs['gain_min'], kwargs['gain_flatmax']
            try: #nf_min and nf_max are expected for a variable gain amp
                nf_min = kwargs.pop('nf_min')
                nf_max = kwargs.pop('nf_max')
            except KeyError:
                print(f'missing nf_min/max value input for amplifier: {type_variety} in eqpt_config.json')
                exit()
            try: #remove all remaining nf inputs
                del kwargs['nf0']
            except KeyError: pass #nf0 is not needed for variable gain amp
            nf1, nf2, delta_p = nf_model(type_variety, gain_min, gain_max, nf_min, nf_max)
            nf_def = Model_vg(nf1, nf2, delta_p)
        return cls(**{**kwargs, **json_data, 'nf_model': nf_def})


def nf_model(type_variety, gain_min, gain_max, nf_min, nf_max):
    if nf_min < -10:
        print(f'Invalid nf_min value {nf_min!r} for amplifier {type_variety}')
        exit()
    if nf_max < -10:
        print(f'Invalid nf_max value {nf_max!r} for amplifier {type_variety}')
        exit()

    # NF estimation model based on nf_min and nf_max
    # delta_p:  max power dB difference between first and second stage coils
    # dB g1a:   first stage gain - internal VOA attenuation
    # nf1, nf2: first and second stage coils
    #           calculated by solving nf_{min,max} = nf1 + nf2 / g1a{min,max}
    delta_p = 5
    g1a_min = gain_min - (gain_max - gain_min) - delta_p
    g1a_max = gain_max - delta_p
    nf2 = lin2db((db2lin(nf_min) - db2lin(nf_max)) /
                 (1/db2lin(g1a_max) - 1/db2lin(g1a_min)))
    nf1 = lin2db(db2lin(nf_min) - db2lin(nf2)/db2lin(g1a_max))

    if nf1 < 4:
        print(f'First coil value too low {nf1} for amplifier {type_variety}')
        exit()

    # Check 1 dB < delta_p < 6 dB to ensure nf_min and nf_max values make sense.
    # There shouldn't be high nf differences between the two coils:
    #    nf2 should be nf1 + 0.3 < nf2 < nf1 + 2
    # If not, recompute and check delta_p
    if not nf1 + 0.3 < nf2 < nf1 + 2:
        nf2 = clip(nf2, nf1 + 0.3, nf1 + 2)
        g1a_max = lin2db(db2lin(nf2) / (db2lin(nf_min) - db2lin(nf1)))
        delta_p = gain_max - g1a_max
        g1a_min = gain_min - (gain_max-gain_min) - delta_p
        if not 1 < delta_p < 6:
            print(f'Computed \N{greek capital letter delta}P invalid \
                \n 1st coil vs 2nd coil calculated DeltaP {delta_p:.2f} for \
                \n amplifier {type_variety} is not valid: revise inputs \
                \n calculated 1st coil NF = {nf1:.2f}, 2nd coil NF = {nf2:.2f}')
            exit()
    # Check calculated values for nf1 and nf2
    calc_nf_min = lin2db(db2lin(nf1) + db2lin(nf2)/db2lin(g1a_max))
    if not isclose(nf_min, calc_nf_min, abs_tol=0.01):
        print(f'nf_min does not match calc_nf_min, {nf_min} vs {calc_nf_min} for amp {type_variety}')
        exit()
    calc_nf_max = lin2db(db2lin(nf1) + db2lin(nf2)/db2lin(g1a_min))
    if not isclose(nf_max, calc_nf_max, abs_tol=0.01):
        print(f'nf_max does not match calc_nf_max, {nf_max} vs {calc_nf_max} for amp {type_variety}')
        exit()

    return nf1, nf2, delta_p

def edfa_nf(gain_target, variety_type, equipment):
    amp_params = equipment['Edfa'][variety_type]
    amp = Edfa(
            uid = f'calc_NF',
            params = amp_params._asdict(),
            operational = {
                'gain_target': gain_target,
                'tilt_target': 0,
            })
    return amp._calc_nf(True)

def trx_mode_params(equipment, trx_type_variety='', trx_mode='', error_message=False):
    """return the trx and SI parameters from eqpt_config for a given type_variety and mode (ie format)"""
    trx_params = {}
    default_si_data = equipment['SI']['default']
    try:
        trxs = equipment['Transceiver']
        mode_params = next(mode for trx in trxs \
                    if trx == trx_type_variety \
                    for mode in trxs[trx].mode \
                    if mode['format'] == trx_mode)
        trx_params = {**mode_params}
        trx_params['frequency'] = equipment['Transceiver'][trx_type_variety].frequency
        # TODO: novel automatic feature maybe unwanted if spacing is specified
        trx_params['spacing'] = automatic_spacing(trx_params['baud_rate'])
    except StopIteration :
        if error_message:
            print(f'could not find tsp : {trx_type_variety} with mode: {trx_mode} in eqpt library')
            print('Computation stopped.')
            exit()
        else:
            # default transponder charcteristics
            trx_params['frequency'] = {'min': default_si_data.f_min, 'max': default_si_data.f_max}
            trx_params['baud_rate'] = default_si_data.baud_rate
            trx_params['spacing'] = default_si_data.spacing
            trx_params['OSNR'] = default_si_data.OSNR
            trx_params['bit_rate'] = default_si_data.bit_rate
            trx_params['roll_off'] = default_si_data.roll_off
    trx_params['power'] =  db2lin(default_si_data.power_dbm)*1e-3
    trx_params['nb_channel'] = automatic_nch(trx_params['frequency']['min'],
                                             trx_params['frequency']['max'],
                                             trx_params['spacing'])
    print('N channels = ', trx_params['nb_channel'])
    return trx_params

def automatic_spacing(baud_rate):
    """return the min possible channel spacing for a given baud rate"""
    spacing_list = [(38e9,50e9), (67e9,75e9), (92e9,100e9)] #list of possible tuples
                                                #[(max_baud_rate, spacing_for_this_baud_rate)]
    acceptable_spacing_list = list(filter(lambda x : x[0]>baud_rate, spacing_list))
    if len(acceptable_spacing_list) < 1:
        #can't find an adequate spacing from the list, so default to:
        return baud_rate*1.2
    else:
        #chose the lowest possible spacing
        return min(acceptable_spacing_list, key=itemgetter(0))[1]

def automatic_nch(f_min, f_max, spacing):
    return int((f_max - f_min)//spacing)

def load_equipment(filename):
    json_data = load_json(filename)
    return equipment_from_json(json_data, filename)

def equipment_from_json(json_data, filename):
    """build global dictionnary eqpt_library that stores all eqpt characteristics:
    edfa type type_variety, fiber type_variety
    from the eqpt_config.json (filename parameter)
    also read advanced_config_from_json file parameters for edfa if they are available:
    typically nf_ripple, dfg gain ripple, dgt and nf polynomial nf_fit_coeff
    if advanced_config_from_json file parameter is not present: use nf_model:
    requires nf_min and nf_max values boundaries of the edfa gain range
    """
    equipment = {}
    for key, entries in json_data.items():
        for entry in entries:
            if key not in equipment:
                equipment[key] = {}
            subkey = entry.get('type_variety', 'default')
            typ = globals()[key]
            if key == 'Edfa':
                if 'advanced_config_from_json' in entry:
                    config = Path(filename).parent / entry.pop('advanced_config_from_json')
                    typ = lambda **kws: Amp.from_advanced_json(config, **kws)
                else:
                    config = Path(filename).parent / 'default_edfa_config.json'
                    typ = lambda **kws: Amp.from_default_json(config, **kws)
            equipment[key][subkey] = typ(**entry)
    return equipment
