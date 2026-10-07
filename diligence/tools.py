"""Small, read-only tools for Qwen to retrieve checkpointed slide interpretations."""
import json
import re


def definition(name, description, properties, required):
    return dict(type='function', function=dict(name=name, description=description,
        parameters=dict(type='object', properties=properties, required=required, additionalProperties=False)))


TOOLS = [
    definition('read_slide', 'Read a bounded excerpt of the combined text-and-vision interpretation of one processed slide. Truncation is explicitly marked. Use to resolve an unclear claim before writing the business explanation.',
        {'slide_number': {'type': 'integer', 'minimum': 1, 'maximum': 150}}, ['slide_number']),
    definition('search_slides', 'Search processed slide interpretations for a focused keyword topic. Returns up to 3 matches with slide numbers. No internet access.',
        {'query': {'type': 'string', 'maxLength': 200}}, ['query']),
]


def compact_slide(slide):
    return {'slide_number': slide['number'], **slide['interpretation']}


def bounded(slides, max_bytes=2400):
    result = {'slides': slides}
    while len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes:
        strings = []
        for slide in result['slides']:
            for key, value in slide.items():
                if isinstance(value, str) and len(value) > 40:
                    strings.append((len(value), slide, key))
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, str) and len(item) > 40:
                            strings.append((len(item), value, index))
        if not strings:
            result['slides'] = result['slides'][:1]
            result['truncated'] = True
            break
        _, container, key = max(strings, key=lambda item: item[0])
        container[key] = container[key][:len(container[key])//2] + '…'
        result['truncated'] = True
    return result


def execute(slides, name, arguments):
    if isinstance(arguments, str):
        if len(arguments) > 1000:
            raise ValueError('Tool arguments are too large.')
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError('Tool arguments must be an object.')
    if name == 'read_slide':
        if set(arguments) != {'slide_number'} or type(arguments['slide_number']) is not int:
            raise ValueError('read_slide requires one integer slide_number.')
        found = next((s for s in slides if s['number'] == arguments['slide_number'] and s.get('interpretation')), None)
        if found is None:
            raise ValueError('That slide has not been processed.')
        # Copy before bounding; persisted full interpretations must never be modified.
        return bounded(json.loads(json.dumps([compact_slide(found)])))
    if name == 'search_slides':
        if set(arguments) != {'query'} or not isinstance(arguments['query'], str) or not 1 <= len(arguments['query']) <= 200:
            raise ValueError('search_slides requires a query of 1–200 characters.')
        terms = set(re.findall(r'\w+', arguments['query'].lower()))
        if not terms:
            return {'slides': []}
        scored = []
        for slide in slides:
            if not slide.get('interpretation'):
                continue
            words = set(re.findall(r'\w+', json.dumps(slide['interpretation']).lower()))
            score = len(terms & words)
            if score:
                scored.append((score, slide))
        scored.sort(key=lambda item: (-item[0], item[1]['number']))
        return bounded(json.loads(json.dumps([compact_slide(s) for _, s in scored[:3]])))
    raise ValueError('Unknown tool. Only read_slide and search_slides are available.')
