from docx import Document
from mynotes.publication import _append_inline, _inline_math_tokens


def rendered(line):
    doc=Document();paragraph=doc.add_paragraph();_append_inline(paragraph,line)
    return paragraph, ''.join(paragraph._p.xpath('.//w:t/text() | .//m:t/text()'))


def test_ordinary_currency_does_not_turn_into_inline_math():
    line='Fixed costs are $100, price is $10 and variable cost is $5. Margin is $5 per unit ($10 - $5).'
    paragraph,text=rendered(line)
    assert text == line
    assert not paragraph._p.xpath('.//m:oMath')


def test_currency_before_equation_does_not_consume_its_opening_dollar():
    paragraph,text=rendered(r'Revenue is $200 ($10 \times 20$).')
    assert text == 'Revenue is $200 (10 × 20).'
    assert len(paragraph._p.xpath('.//m:oMath')) == 1


def test_escaped_currency_inside_math_is_not_a_closing_delimiter():
    paragraph,text=rendered(r'Revenue ($10 \times 20 = \$200$) equals costs.')
    assert text == 'Revenue (10 × 20 = $200) equals costs.'
    assert len(paragraph._p.xpath('.//m:oMath')) == 1


def test_actual_agy_answer_keeps_currency_inside_both_equations():
    line=r'At 20 units, total revenue ($10 \times 20 = \$200$) exactly matches total costs ($100 \text{ fixed} + 5 \times 20 = \$200$), yielding a net profit of zero.'
    paragraph,text=rendered(line)
    assert '$200' in text and text.count('$200') == 2
    assert '100  fixed + 5 × 20 = $200' in text
    assert len(paragraph._p.xpath('.//m:oMath')) == 2
    assert '\\' not in text


def test_bold_currency_prose_and_escaped_plain_dollars_keep_formatting():
    paragraph,text=rendered(r'**Fixed costs: $100**; no more than \$200.')
    assert text == 'Fixed costs: $100; no more than $200.'
    assert any(run.bold and '$100' in run.text for run in paragraph.runs)
    assert not paragraph._p.xpath('.//m:oMath')


def test_regular_inline_formula_and_display_formula_stay_native():
    paragraph,text=rendered(r'Use $Q=\frac{F}{c}$ or $$Q=20$$.')
    assert len(paragraph._p.xpath('.//m:oMath')) == 2
    assert paragraph._p.xpath('.//m:f')
    assert '$' not in text


def test_rejected_currency_opening_leaves_next_expression_available():
    assert _inline_math_tokens(r'Total costs are $200 ($100 + $5 \times 20$).') == [r'$5 \times 20$']
    paragraph,text=rendered(r'Total costs are $200 ($100 + $5 \times 20$).')
    assert text == 'Total costs are $200 ($100 + 5 × 20).'
    assert len(paragraph._p.xpath('.//m:oMath')) == 1


def test_actual_codex_currency_prose_keeps_all_amounts_literal():
    line='Each unit contributes $5 toward the $100 of fixed costs. The workshop therefore needs to sell 20 units to break even.'
    paragraph,text=rendered(line)
    assert text == line
    assert not paragraph._p.xpath('.//m:oMath')
    answer='Each unit contributes $5 toward fixed costs. Selling 20 units provides $100 of contribution, exactly covering the $100 of fixed costs. Revenue and total costs are both $200, so profit is zero.'
    paragraph,text=rendered(answer)
    assert text == answer
    assert not paragraph._p.xpath('.//m:oMath')
