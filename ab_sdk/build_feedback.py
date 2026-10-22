"""Public, allowlisted build diagnostics. No neural implementation belongs here."""
import re

SCHEMA = 'artificial-brains/build-feedback/v1'
MAX_BYTES = 16384
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
MESSAGES = {
    'CONSTRUCTION_OK': 'Graph construction completed.',
    'INVALID_DESIGN': 'Check the design fields, connection endpoints and numeric values.',
    'MISSING_INPUT': 'Add a sensory input to the design.',
    'NO_EXCITATORY_PATH': 'The named populations have no construction path from an input. Inhibition and feedback alone cannot establish that path.',
    'AMBIGUOUS_FEEDBACK': 'The recurrent connections do not identify an unambiguous construction path. Mark the intended return connections as feedback.',
    'OPERAND_IS_RETURN': 'An operand connection is also a recurrent return. Identify the intended feedback connection explicitly.',
    'MIXED_OPERAND_ROLES': 'This target mixes operand connections with ordinary excitation. Mark every excitatory operand into that target as an operand.',
    'MIXED_SOURCE_RHYTHMS': 'The immediate sources into this target require compatible reference rhythms. Use suitable relay OPUs before combining them.',
    'NO_CONSTRUCTION_EXCITATION': 'This OPU needs a construction excitation source.',
    'POPULATION_CAPACITY': 'A population exceeds the existing device population capacity. Review the input widths and sources feeding the named target.',
    'NEURON_CAPACITY': 'The design exceeds the existing 10,000-neuron construction budget, including input channels. Reduce its total allocation.',
    'OPERATING_BAND': 'The selected source and target combination has no supported operating band. Review their rhythms and source widths; changing TNorm alone does not create a valid band.',
    'OUTPUT_SOURCE': 'The existing bistable output requires excitation and accepts beta or high-gamma OPU sources. Review the named output connection.',
    'INVALID_UNITS': 'Operand units must be a positive finite number on a connection with role operand.',
    'INVALID_STRENGTH': 'Inhibitory strength must be a finite number of at least 0.1.',
    'INVALID_TNORM': 'TNorm must be a finite number from 0 to 10.',
    'DUPLICATE_CONNECTION': 'The same source and target are connected more than once.',
    'INVALID_DIRECTION': 'Check the connection direction: inputs excite OPUs; outputs can feed back into OPUs.',
    'MATRIX_CAPACITY': 'The connection matrices exceed the existing prepared-image budget. Reduce the number or width of the connected populations.',
    'PACKAGE_PREPARATION_FAILED': 'Package preparation failed. Check the aggregate resource counts. They are not a complete measurement of the prepared image.',
    'BUILD_TIME_LIMIT': 'The server stopped waiting for this build. The design may require more construction or compilation time.',
}
RESOURCE_KEYS = {
    'design_populations', 'design_connections', 'input_neurons',
    'neural_neurons', 'total_neurons', 'neuron_budget',
    'dense_matrix_cells', 'matrix_storage_bytes', 'image_budget_bytes',
}

def sanitize(value, digest, names=None):
    """Reconstruct from known fields. Never forward a supplied message string."""
    if (not isinstance(value, dict) or not isinstance(digest, str)
            or not SHA.fullmatch(digest) or value.get('schema') != SCHEMA
            or value.get('design_sha256') != digest
            or not isinstance(value.get('code'), str) or value['code'] not in MESSAGES):
        return None
    result = {'schema': SCHEMA, 'design_sha256': digest, 'code': value['code']}
    items = value.get('populations', [])
    if not isinstance(items, list):
        return None
    result['populations'] = list(dict.fromkeys(n for n in items[:8]
        if isinstance(n, str) and NAME.fullmatch(n) and (names is None or n in names)))
    resources = value.get('resources', {})
    if not isinstance(resources, dict):
        return None
    result['resources'] = {k: v for k, v in resources.items()
        if k in RESOURCE_KEYS and type(v) is int and 0 <= v <= 9007199254740991}
    return result

def describe(feedback):
    text = '[' + feedback['code'] + '] ' + MESSAGES[feedback['code']]
    if feedback.get('populations'):
        text += ' Populations: ' + ', '.join(feedback['populations']) + '.'
    r = feedback.get('resources', {})
    if 'total_neurons' in r:
        text += f" Total allocation: {r['total_neurons']:,}"
        if 'neuron_budget' in r:
            text += f" / {r['neuron_budget']:,}"
        text += ' neurons'
        if 'input_neurons' in r and 'neural_neurons' in r:
            text += f" ({r['input_neurons']:,} inputs; {r['neural_neurons']:,} OPUs and outputs)"
        text += '.'
    if 'dense_matrix_cells' in r:
        text += f" Connection matrices: {r['dense_matrix_cells']:,} dense cells"
        if 'matrix_storage_bytes' in r:
            text += f"; {r['matrix_storage_bytes']/1048576:.2f} MiB of matrix storage"
        if 'image_budget_bytes' in r:
            text += f" within a {r['image_budget_bytes']/1048576:.0f} MiB image budget"
        text += '. Other image data also uses this budget.'
    return text