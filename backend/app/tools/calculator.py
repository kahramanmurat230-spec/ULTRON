import ast, math, operator, re

_ALLOWED = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}

def _safe_eval(expr):
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError as exc:
        raise ValueError("Hesaplama ifadesi anlaşılamadı") from exc
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int,float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _ALLOWED:
            return _ALLOWED[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _ALLOWED:
            return _ALLOWED[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sqrt" and len(n.args)==1:
            return math.sqrt(ev(n.args[0]))
        raise ValueError("İzin verilmeyen matematik ifadesi")
    return ev(node)

def calculate(text):
    t=text.lower().replace(",", ".")
    # Unicode operators ("37×24", "8÷2") and digit-x-digit ("2x3") → real operators.
    t=t.replace("×","*").replace("÷","/").replace("·","*")
    t=re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", t)
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ile|ve)\s*(\d+(?:\.\d+)?)\s*['’]?(?:yi|yı|yu|yü|ı|i|u|ü)?\s*topla", t)
    if m: return f"{float(m.group(1))+float(m.group(2)):g}"
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ile|ve)\s*(\d+(?:\.\d+)?)\s*(?:çıkar|çıkart)", t)
    if m: return f"{float(m.group(1))-float(m.group(2)):g}"
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ile|ve)\s*(\d+(?:\.\d+)?)\s*çarp", t)
    if m: return f"{float(m.group(1))*float(m.group(2)):g}"
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ile|ve)\s*(\d+(?:\.\d+)?)\s*b[öo]l", t)
    if m: return f"{float(m.group(1))/float(m.group(2)):g}"
    m=re.search(r"(\d+(?:\.\d+)?)'?(?:nin|nın|nun|nün|in|ın|un|ün)?\s*yüzde\s*(\d+(?:\.\d+)?)", t)
    if m: return f"{float(m.group(1))*float(m.group(2))/100:g}"
    m=re.search(r"(\d+(?:\.\d+)?)'?(?:nın|nin|nun|nün|ın|in|un|ün)?\s*karek[öo]k[üu]", t)
    if m: return f"{math.sqrt(float(m.group(1))):g}"
    # Longest contiguous arithmetic span (parens included) instead of stripping
    # every non-numeric char — stripping dropped × and left stray dots that
    # crashed eval with SyntaxError. Evaluated FIRST so real expressions keep
    # operator precedence ("(2+3)*4" must not become "2+3+4").
    best=None
    for m in re.finditer(r"[0-9+\-*/%().\s]+", t):
        s=m.group(0).strip(" .")
        # must start with a digit/paren AND contain a binary operator between
        # numbers — prose fragments like " + 2" must not evaluate as unary plus.
        if (re.match(r"^[\d(]", s) and re.search(r"\d\s*[+\-*/%]\s*[\d(]", s)
                and (best is None or len(s)>len(best))):
            best=s
    if best:
        try: return f"{_safe_eval(best):g}"
        except (SyntaxError, ValueError): pass
    # Fallback: scattered numbers joined with "+" ("3 elma + 2 armut" style sums).
    nums=re.findall(r"\d+(?:\.\d+)?", t)
    if len(nums)>=2 and "+" in t: return f"{_safe_eval('+'.join(nums)):g}"
    raise ValueError("Hesaplama ifadesi anlaşılamadı")
