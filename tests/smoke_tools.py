"""Opt-in real local Qwen function-call test using synthetic evidence only."""
import json
from diligence.lm import LMStudio
from diligence.tools import TOOLS, execute


if __name__ == '__main__':
    lm = LMStudio()
    model = lm.models()[0]
    messages = [
        {'role': 'system', 'content': 'Use the supplied tools. Do not invent their output.'},
        {'role': 'user', 'content': 'Call read_slide with slide_number 1 to get the saved interpretation. Do not answer until you have used that tool.'}
    ]
    message, usage = lm.chat(model, messages, tools=TOOLS, max_tokens=600)
    calls = message.get('tool_calls') or []
    if not calls:
        raise SystemExit('No parsed tool call returned by local Qwen.')
    messages.append({'role': 'assistant', 'content': message.get('content'), 'tool_calls': calls})
    fixture = [{'number': 1, 'interpretation': {'title': 'Synthetic fixture', 'explanation': 'This synthetic fixture presents a solar inspection product.', 'claims': ['A product is proposed.'], 'uncertainties': []}}]
    for call in calls:
        result = execute(fixture, call['function']['name'], call['function']['arguments'])
        messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(result)})
    messages.append({'role': 'user', 'content': 'What kind of product does the synthetic fixture present? Answer in one sentence.'})
    final, final_usage = lm.chat(model, messages, max_tokens=600)
    if not final.get('content'):
        raise SystemExit('Local Qwen did not respond after receiving the tool result.')
    print(json.dumps({'native_tool_calls': len(calls), 'tool_results_accepted': True, 'final_response_present': True,
                      'reasoning_tokens': usage.get('completion_tokens_details', {}).get('reasoning_tokens')}))
