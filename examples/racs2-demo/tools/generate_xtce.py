#!/usr/bin/env python3
"""Generate a Yamcs XTCE MDB from an upstream cFS native_std build.

Uses the build's headers/macros and GCC/GDB debug types for actual ABI offsets.
Only temporary probe files and the explicitly selected output files are written.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET

NS = 'http://www.omg.org/spec/XTCE/20180204'
ET.register_namespace('', NS)
MODULES = ['cfe_es', 'cfe_evs', 'cfe_sb', 'cfe_tbl', 'cfe_time', 'to_lab', 'ci_lab', 'sample_app']
# SC-OBC CPUs and the upstream build target with the same message ID blocks
# (scobc_msgid_bases.h): the RPU uses 0x18xx/0x08xx, the APU 0x19xx/0x09xx.
CPUS = [('RPU', 'default_cpu1'), ('APU', 'default_cpu2')]
# Explicit associations where packet type names differ from MID names.
TELEMETRY = {
    'cfe_es': [('Housekeeping', 'HK_TLM'), ('OneApp', 'APP_TLM'), ('MemStats', 'MEMSTATS_TLM')],
    'cfe_evs': [('Housekeeping', 'HK_TLM'), ('LongEvent', 'LONG_EVENT_MSG'), ('ShortEvent', 'SHORT_EVENT_MSG')],
    'cfe_sb': [('Housekeeping', 'HK_TLM'), ('Stats', 'STATS_TLM'),
               ('AllSubscriptions', 'ALLSUBS_TLM'), ('SingleSubscription', 'ONESUB_TLM')],
    'cfe_tbl': [('Housekeeping', 'HK_TLM'), ('TableRegistry', 'REG_TLM')],
    'cfe_time': [('Housekeeping', 'HK_TLM'), ('Diagnostic', 'DIAG_TLM')],
    'to_lab': [('Hk', 'HK_TLM'), ('DataTypes', 'DATA_TYPES')],
    'ci_lab': [('Hk', 'HK_TLM')],
    'sample_app': [('Hk', 'HK_TLM')],
}


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs)


def element(parent, tag, **attrs):
    return ET.SubElement(parent, '{%s}%s' % (NS, tag), {k: str(v) for k, v in attrs.items()})


def locate(entry, offset):
    location = element(entry, 'LocationInContainerInBits', referenceLocation='containerStart')
    element(location, 'FixedValue').text = str(offset * 8)


def safe_name(name):
    return re.sub(r'[^A-Za-z0-9_]', '_', name).rstrip('_')


def payload_fields(layout):
    return [f for f in layout['fields'] if not f['path'].startswith(('CommandHeader.', 'TelemetryHeader.'))]


def make_fixtures(include, flags, manifest, temp):
    """Independent packet bytes: initialize real upstream C structures with GCC."""
    code = [include, '#include <stdio.h>', '#include <string.h>', 'int main(void) {']
    fixtures = []
    for index, packet in enumerate(manifest['commands'] + manifest['telemetry']):
        layout = manifest['layouts'][packet['c_type']]
        var = 'packet_%d' % index
        code.append('%s %s = {0};' % (packet['c_type'], var))
        values = {}
        for field in payload_fields(layout):
            path = field['path']
            if field['kind'] == 'string':
                value = 'CFS'[:max(0, field['size'] - 1)]
                code.append('memcpy(%s.%s, "%s", %d);' % (var, path, value, len(value)))
            else:
                value = (1 if field.get('boolean') else -3.25 if field['kind'] == 'float'
                         else -2 if field['kind'] == 'signed' else 90)
                code.append('%s.%s = %s;' % (var, path, value))
            values[safe_name(path)] = value
        is_command = 'code' in packet
        header = packet['mid'].to_bytes(2, 'big') + bytes.fromhex('C000')
        header += (layout['size'] - 7).to_bytes(2, 'big')
        header += bytes([packet['code'], 0]) if is_command else bytes.fromhex('00000001000000000000')
        code.append('const unsigned char header_%d[] = {%s};' %
                    (index, ','.join(str(b) for b in header)))
        code.append('memcpy(&%s, header_%d, sizeof(header_%d));' % (var, index, index))
        code.append('for (size_t i=0; i<sizeof(%s); ++i) { printf("%%02x", ((unsigned char *)&%s)[i]); }\nputs("");' % (var, var))
        fixtures.append(dict(cpu=manifest['cpu'], name=packet['name'], kind='command' if is_command else 'telemetry', values=values))
    code.append('return 0; }')
    src = temp / 'fixtures.c'
    src.write_text('\n'.join(code))
    binary = temp / 'fixtures'
    run(['gcc', *flags, str(src), '-o', str(binary)])
    hex_packets = run([str(binary)]).splitlines()
    if len(hex_packets) != len(fixtures):
        raise ValueError('Unexpected number of C fixture packets')
    for fixture, wire in zip(fixtures, hex_packets):
        fixture['hex'] = wire
    return fixtures


def generate(manifest, output, module=None):
    """Common root or one application, mounted beneath /cFS by subLoaders."""
    system = module.upper() if module else 'cFS'
    root = ET.Element('{%s}SpaceSystem' % NS, name=system,
                      shortDescription='Upstream cFS native_std ' + manifest['cpu'] + ': ' + (system if module else 'common headers'))
    element(root, 'Header', version='1.0', validationStatus='Draft')
    tm = element(root, 'TelemetryMetaData')
    types = element(tm, 'ParameterTypeSet')
    params = element(tm, 'ParameterSet')
    containers = element(tm, 'ContainerSet')
    tc = element(root, 'CommandMetaData')
    args = element(tc, 'ArgumentTypeSet')
    commands = element(tc, 'MetaCommandSet')
    defined = {'Parameter': set(), 'Argument': set()}

    def datatype(f, category, byte_order=None):
        order = byte_order or manifest['byte_order']
        name = '%s%d_%s' % (f['kind'], f['size'] * 8, order)
        if name in defined[category]:
            return name
        defined[category].add(name)
        parent = types if category == 'Parameter' else args
        bits = f['size'] * 8
        if f['kind'] in ('unsigned', 'signed'):
            node = element(parent, 'Integer' + category + 'Type', name=name,
                           signed=str(f['kind'] == 'signed').lower(), sizeInBits=bits)
            element(node, 'IntegerDataEncoding', sizeInBits=bits, byteOrder=order,
                    encoding='twosComplement' if f['kind'] == 'signed' else 'unsigned')
        elif f['kind'] == 'float':
            node = element(parent, 'Float' + category + 'Type', name=name, sizeInBits=bits)
            element(node, 'FloatDataEncoding', sizeInBits=bits, byteOrder=order, encoding='IEEE754_1985')
        elif f['kind'] == 'string':
            node = element(parent, 'String' + category + 'Type', name=name)
            enc = element(node, 'StringDataEncoding', encoding='UTF-8')
            size = element(enc, 'SizeInBits')
            element(element(size, 'Fixed'), 'FixedValue').text = str(bits)
            element(size, 'TerminationChar').text = '00'
        else:
            raise ValueError(f)
        return name

    if not module:
        for bits in (1, 2, 3, 11, 14, 16, 32):
            node = element(types, 'IntegerParameterType', name='header_uint%d' % bits,
                           signed='false', sizeInBits=bits)
            element(node, 'IntegerDataEncoding', sizeInBits=bits, encoding='unsigned',
                    byteOrder='mostSignificantByteFirst')

        def tm_header(container, fields):
            entries = element(container, 'EntryList')
            for name, bits, bit_offset in fields:
                element(params, 'Parameter', name=name, parameterTypeRef='header_uint%d' % bits)
                entry = element(entries, 'ParameterRefEntry', parameterRef=name)
                location = element(entry, 'LocationInContainerInBits', referenceLocation='containerStart')
                element(location, 'FixedValue').text = str(bit_offset)

        primary = element(containers, 'SequenceContainer', name='CCSDS_PrimaryHeader',
                          shortDescription='Six-byte CCSDS primary header; MID and Sequence retain the full words')
        tm_header(primary, [('MessageId', 16, 0), ('Version', 3, 0), ('PacketType', 1, 3),
                            ('SecondaryHeaderFlag', 1, 4), ('APID', 11, 5),
                            ('Sequence', 16, 16), ('SequenceFlags', 2, 16),
                            ('SequenceCount', 14, 18), ('PacketLength', 16, 32)])
        base = element(containers, 'SequenceContainer', name='CCSDS_Telemetry',
                       shortDescription='cFS telemetry time header and four spare bytes')
        tm_header(base, [('Seconds', 32, 48), ('Subseconds', 16, 80), ('Spare', 32, 96)])
        inheritance = element(base, 'BaseContainer', containerRef='CCSDS_PrimaryHeader')
        criteria = element(inheritance, 'RestrictionCriteria')
        comparisons = element(criteria, 'ComparisonList')
        element(comparisons, 'Comparison', parameterRef='PacketType', value='0')
        element(comparisons, 'Comparison', parameterRef='SecondaryHeaderFlag', value='1')

        # Reusable CCSDS/cFS command header. MID includes the version, packet
        # type, secondary-header flag and APID, as defined by cFE's 16-bit MID.
        for bits in (2, 8, 14, 16):
            node = element(args, 'IntegerArgumentType', name='header_uint%d' % bits,
                           signed='false', sizeInBits=bits)
            element(node, 'IntegerDataEncoding', sizeInBits=bits, encoding='unsigned',
                    byteOrder='mostSignificantByteFirst')
        base_command = element(commands, 'MetaCommand', name='CCSDS_Command', abstract='true',
                               shortDescription='CCSDS primary header and cFS command secondary header')
        arguments = element(base_command, 'ArgumentList')
        header_fields = [('mid', 16, 0, None), ('sequenceFlags', 2, 16, '3'),
                         ('sequenceCount', 14, 18, '0'), ('packetLength', 16, 32, None),
                         ('functionCode', 8, 48, None), ('checksum', 8, 56, '0')]
        container = element(base_command, 'CommandContainer', name='CCSDS_Command')
        entries = element(container, 'EntryList')
        for name, bits, bit_offset, default in header_fields:
            attributes = dict(name=name, argumentTypeRef='header_uint%d' % bits)
            if default is not None:
                attributes['initialValue'] = default
            element(arguments, 'Argument', **attributes)
            entry = element(entries, 'ArgumentRefEntry', argumentRef=name)
            location = element(entry, 'LocationInContainerInBits', referenceLocation='containerStart')
            element(location, 'FixedValue').text = str(bit_offset)

    for packet in manifest['telemetry']:
        if not module or not packet['name'].startswith(system + '_'):
            continue
        layout = manifest['layouts'][packet['c_type']]
        container = element(containers, 'SequenceContainer', name=packet['name'],
                            shortDescription='%s; MID=0x%04X; sizeof=%d' %
                            (packet['c_type'], packet['mid'], layout['size']))
        entry_list = element(container, 'EntryList')
        for f in payload_fields(layout):
            name = packet['name'] + '_' + safe_name(f['path'])
            element(params, 'Parameter', name=name, parameterTypeRef=datatype(f, 'Parameter'),
                    shortDescription=f['path'] + ' (' + f['c_type'] + ')')
            entry = element(entry_list, 'ParameterRefEntry', parameterRef=name)
            locate(entry, f['offset'])
        inheritance = element(container, 'BaseContainer', containerRef='/cFS/CCSDS_Telemetry')
        criteria = element(inheritance, 'RestrictionCriteria')
        element(criteria, 'Comparison', parameterRef='/cFS/MessageId', value='0x%04X' % packet['mid'])

    for cmd in manifest['commands']:
        if not module or not cmd['name'].startswith(system + '_'):
            continue
        layout = manifest['layouts'][cmd['c_type']]
        node = element(commands, 'MetaCommand', name=cmd['name'],
                       shortDescription='%s; MID=0x%04X; FC=%d; sizeof=%d' %
                       (cmd['c_type'], cmd['mid'], cmd['code'], layout['size']))
        inheritance = element(node, 'BaseMetaCommand', metaCommandRef='/cFS/CCSDS_Command')
        assignments = element(inheritance, 'ArgumentAssignmentList')
        for name, value in [('mid', '0x%04X' % cmd['mid']),
                            ('packetLength', str(layout['size'] - 7)),
                            ('functionCode', str(cmd['code'])), ('sequenceFlags', '3'),
                            ('sequenceCount', '0'), ('checksum', '0')]:
            element(assignments, 'ArgumentAssignment', argumentName=name, argumentValue=value)
        fields = payload_fields(layout)
        arg_list = element(node, 'ArgumentList') if fields else None
        container = element(node, 'CommandContainer', name=cmd['name'])
        entry_list = element(container, 'EntryList')
        # The common base container encodes the header fields. The link
        # postprocessor updates length, sequence count and checksum on send.
        cursor = 8
        for f in fields:
            if f['offset'] < cursor:
                raise ValueError('Overlapping fields in ' + cmd['name'])
            if f['offset'] > cursor:
                gap = f['offset'] - cursor
                element(entry_list, 'FixedValueEntry', name='Padding_%d' % cursor,
                        binaryValue='00' * gap, sizeInBits=gap * 8)
            name = safe_name(f['path'])
            attributes = dict(name=name, argumentTypeRef=datatype(f, 'Argument'))
            if re.search(r'(?:Spare|spare|Padding|padding|Reserved|reserved)', f['path']):
                attributes['initialValue'] = '' if f['kind'] == 'string' else '0'
            element(arg_list, 'Argument', **attributes)
            entry = element(entry_list, 'ArgumentRefEntry', argumentRef=name)
            locate(entry, f['offset'])
            cursor = f['offset'] + f['size']
        if cursor < layout['size']:
            gap = layout['size'] - cursor
            element(entry_list, 'FixedValueEntry', name='TrailingPadding', binaryValue='00' * gap,
                    sizeInBits=gap * 8)
        element(container, 'BaseContainer', containerRef='/cFS/CCSDS_Command')
    if module and not len(args):
        tc.remove(args)
    ET.indent(root, space='  ')
    ET.ElementTree(root).write(output, encoding='UTF-8', xml_declaration=True)


def inspect_cpu(opts, cpu, target):
    source = opts.cfs_root.resolve()
    build = (opts.build_dir or source / 'build-native_std').resolve()
    flags_file = build / ('native/' + target + '/es/CMakeFiles/es.dir/flags.make')
    flags_text = flags_file.read_text()
    flags = []
    for key in ('C_DEFINES', 'C_INCLUDES', 'C_FLAGS'):
        flags.extend(shlex.split(re.search(r'^' + key + r' = (.*)$', flags_text, re.M).group(1)))
    for app in ('to_lab', 'ci_lab', 'sample_app'):
        flags.append('-I' + str(source / 'apps' / app / 'fsw/inc'))
    include = '\n'.join('#include "%s_%s.h"' % (module, suffix)
                        for module in MODULES for suffix in ('msg', 'msgids'))
    commands, telemetry = [], []
    with tempfile.TemporaryDirectory(prefix='cfs-xtce-') as temp:
        temp = Path(temp)
        probe = temp / 'probe.c'
        probe.write_text(include + '\n')
        preprocessed = run(['gcc', *flags, '-E', '-P', str(probe)])
        available = set(re.findall(r'\}\s*(\w+_t)\s*;', preprocessed))
        for module in MODULES:
            prefix = module.upper()
            directory = source / ('cfe/modules/' + module[4:] if module.startswith('cfe_') else 'apps/' + module)
            dispatch = (directory / 'fsw/src' / (module + '_dispatch.c')).read_text()
            # Use dispatcher associations instead of guessing names from CC macros.
            for match in re.finditer(r'case\s+(\w+_CC)\s*:(.*?)(?=\bcase\b|\bdefault\b)', dispatch, re.S):
                code, body = match.groups()
                type_match = re.search(r'\b(' + prefix + r'_\w+Cmd_t)\b', body)
                if not type_match:
                    raise ValueError('No command structure found for ' + code)
                c_type = type_match.group(1)
                mid = prefix + ('_SUB_RPT_CTRL_MID' if module == 'cfe_sb' and
                                c_type in ('CFE_SB_SendPrevSubsCmd_t', 'CFE_SB_EnableSubReportingCmd_t',
                                           'CFE_SB_DisableSubReportingCmd_t') else '_CMD_MID')
                commands.append(dict(name=c_type[:-2], c_type=c_type, mid_macro=mid, code_macro=code))
            # TBL uses a command-handler table rather than a switch statement.
            for match in re.finditer(r'CFE_TBL_ENTRY\(\s*(\w+_MID)\s*,\s*(\w+_CC)\s*,\s*(\w+Cmd_t)', dispatch):
                mid, code, c_type = match.groups()
                commands.append(dict(name=c_type[:-2], c_type=c_type, mid_macro=mid, code_macro=code))
            if not any(c['name'] == prefix + '_NoopCmd' for c in commands):
                raise ValueError('No ground commands found for ' + module)
            commands.append(dict(name=prefix + '_SendHkCmd', c_type=prefix + '_SendHkCmd_t',
                                 mid_macro=prefix + '_SEND_HK_MID', code_macro=None))
            for type_name, mid_name in TELEMETRY[module]:
                c_type = prefix + '_' + type_name + 'Tlm_t'
                if c_type not in available:
                    raise ValueError('Missing telemetry structure ' + c_type)
                telemetry.append(dict(name=c_type[:-2], c_type=c_type, mid_macro=prefix + '_' + mid_name + '_MID'))
        commands.append(dict(name='CI_LAB_ReadUplinkCmd', c_type='CI_LAB_ReadUplinkCmd_t',
                             mid_macro='CI_LAB_READ_UPLINK_MID', code_macro=None))
        # Internal TIME scheduler/synchronization messages are intentionally named
        # as such and use their own MIDs, never the ground-command MID.
        for name, mid in [('OneHz', 'ONEHZ_CMD'), ('ToneSignal', 'TONE_CMD'),
                          ('FakeTone', 'SEND_CMD'), ('ToneData', 'DATA_CMD')]:
            commands.append(dict(name='CFE_TIME_' + name + 'Cmd', c_type='CFE_TIME_' + name + 'Cmd_t',
                                 mid_macro='CFE_TIME_' + mid + '_MID', code_macro=None))
        names = sorted({p['c_type'] for p in telemetry + commands} |
                       {'CFE_MSG_CommandHeader_t', 'CFE_MSG_TelemetryHeader_t'})
        # Unused compatibility/generic structures are not independent commands.
        unused_templates = {'CFE_ES_FileNameCmd_t', 'CFE_EVS_BitMaskCmd_t', 'CFE_EVS_AppNameCmd_t',
                            'CFE_EVS_AppNameBitMaskCmd_t', 'CFE_EVS_AppNameEventIDCmd_t',
                            'CFE_EVS_AppNameEventIDMaskCmd_t', 'CFE_SB_WriteFileInfoCmd_t',
                            'CFE_SB_RouteCmd_t', 'CFE_TBL_FileNameCmd_t', 'CFE_TBL_TableNameCmd_t',
                            'CFE_TIME_TimeCmd_t', 'CFE_TBL_NotifyCmd_t'}
        uncovered = {t for t in available if t.endswith('Cmd_t') and
                     any(t.startswith(m.upper() + '_') for m in MODULES)} - set(names) - unused_templates
        if uncovered:
            raise ValueError('Command structures missing from MDB: ' + ', '.join(sorted(uncovered)))
        macros = sorted({p['mid_macro'] for p in telemetry + commands} |
                        {p['code_macro'] for p in commands if p['code_macro']})
        content = include + '\n#include <stdio.h>\n'
        content += '\n'.join('%s probe_%d;' % (name, i) for i, name in enumerate(names))
        content += '\nint main(void) {\n'
        content += '\n'.join('printf("%s=%%llu\\n", (unsigned long long)(%s));' % (macro, macro) for macro in macros)
        content += '\nprintf("BYTE_ORDER=%d\\n", __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__);\nreturn 0;\n}\n'
        probe.write_text(content)
        executable = temp / 'probe'
        run(['gcc', *flags, '-g', '-fno-eliminate-unused-debug-types', str(probe), '-o', str(executable)])
        values = dict((key, int(value)) for key, value in (line.split('=') for line in run([str(executable)]).splitlines()))
        types_file = temp / 'types.json'
        layout_file = temp / 'layout.json'
        types_file.write_text(json.dumps(names))
        env = dict(os.environ, CFS_XTCE_TYPE_LIST=str(types_file), CFS_XTCE_LAYOUT_OUTPUT=str(layout_file))
        helper = Path(__file__).with_name('inspect_layout.py')
        run(['gdb', '-nx', '-batch', str(executable), '-ex', 'python exec(open(%r).read())' % str(helper)], env=env)
        layouts = json.loads(layout_file.read_text())
    if layouts['CFE_MSG_CommandHeader_t']['size'] != 8 or layouts['CFE_MSG_TelemetryHeader_t']['size'] != 16:
        raise ValueError('This generator supports the default 8-byte TC / 16-byte TM headers only')
    for packet in telemetry + commands:
        packet['mid'] = values[packet['mid_macro']]
        if packet in commands:
            packet['code'] = values[packet['code_macro']] if packet['code_macro'] else 0
        if not 0 <= packet['mid'] <= 65535:
            raise ValueError('Extended message IDs are unsupported')
    revisions = {name: run(['git', '-C', str(source / path), 'rev-parse', 'HEAD']).strip()
                 for name, path in [('cFS', '.'), ('cFE', 'cfe'), ('to_lab', 'apps/to_lab'), ('ci_lab', 'apps/ci_lab'),
                                    ('sample_app', 'apps/sample_app')]}
    manifest = dict(cpu=cpu, profile='native_std/' + target, revisions=revisions,
                    byte_order='leastSignificantByteFirst' if values['BYTE_ORDER'] else 'mostSignificantByteFirst',
                    telemetry=telemetry, commands=commands, layouts=layouts)
    fixtures = []
    if opts.fixtures_output:
        with tempfile.TemporaryDirectory(prefix='cfs-xtce-fixtures-') as fixture_temp:
            fixtures = make_fixtures(include, flags, manifest, Path(fixture_temp))
    return manifest, fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cfs-root', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, help='Defaults to CFS_ROOT/build-native_std')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'src/main/yamcs/mdb/cfs.xml',
                        help='Common root XML; CPU wrappers and application subdirectories are generated beside it')
    parser.add_argument('--fixtures-output', type=Path, help='Optional independent C packet fixtures for verification')
    opts = parser.parse_args()
    inspected = [inspect_cpu(opts, cpu, target) for cpu, target in CPUS]
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    generate(inspected[0][0], opts.output)
    fixtures = []
    for manifest, cpu_fixtures in inspected:
        cpu = manifest['cpu']
        directory = opts.output.parent / cpu.lower()
        directory.mkdir(exist_ok=True)
        root = ET.Element('{%s}SpaceSystem' % NS, name=cpu,
                          shortDescription=manifest['profile'])
        element(root, 'Header', version='1.0', validationStatus='Draft')
        ET.indent(root, space='  ')
        ET.ElementTree(root).write(opts.output.parent / (cpu.lower() + '.xml'),
                                   encoding='UTF-8', xml_declaration=True)
        for module in MODULES:
            generate(manifest, directory / (module + '.xml'), module)
        (directory / 'cfs.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        fixtures.extend(cpu_fixtures)
        print('%s: %d telemetry packets, %d commands' %
              (cpu, len(manifest['telemetry']), len(manifest['commands'])))
    index = dict(profiles=[m['profile'] for m, _ in inspected],
                 revisions=inspected[0][0]['revisions'],
                 manifests={m['cpu']: m['cpu'].lower() + '/cfs.manifest.json' for m, _ in inspected})
    opts.output.with_suffix('.manifest.json').write_text(json.dumps(index, indent=2) + '\n')
    if opts.fixtures_output:
        opts.fixtures_output.write_text(json.dumps(fixtures, indent=2) + '\n')


if __name__ == '__main__':
    main()
