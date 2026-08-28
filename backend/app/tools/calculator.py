import ast, math, operator, re

_ALLOWED = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}

def _safe_eval(expr):
    node = ast.parse(expr, mode="eval").body
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
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ile|ve)\s*(\d+(?:\.\d+)?)\s*['’]?(?:yi|yı|yu|yü|ı|i|u|ü)?\s*topla", t)
    if m: return f"{float(m.group(1))+float(m.group(2)):g}"
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ile|ve)\s*(\d+(?:\.\d+)?)\s*(?:çıkar|çıkart)", t)
    if m: return f"{float(m.group(1))-float(m.group(2)):g}"
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ile|ve)\s*(\d+(?:\.\d+)?)\s*çarp", t)
    if m: return f"{float(m.group(1))*float(m.group(2)):g}"
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ile|ve)\s*(\d+(?:\.\d+)?)\s*b[öo]l", t)
    if m: return f"{float(m.group(1))/float(m.group(2)):g}"
    m=re.search(r"(\d+(?:\.\d+)?)'?(?:in|ın|un|ün)?\s*yüzde\s*(\d+(?:\.\d+)?)", t)
    if m: return f"{float(m.group(1))*float(m.group(2))/100:g}"
    m=re.search(r"(\d+(?:\.\d+)?)'?(?:ün|un|ın|in)?\s*karek[öo]k[üu]", t)
    if m: return f"{math.sqrt(float(m.group(1))):g}"
    nums=re.findall(r"\d+(?:\.\d+)?", t)
    if len(nums)>=2 and "+" in t: return f"{_safe_eval('+'.join(nums)):g}"
    expr=re.sub(r"[^0-9+*/().%\- ]", "", t)
    if re.search(r"\d", expr) and any(c in expr for c in "+-*/%"):
        return f"{_safe_eval(expr):g}"
    raise ValueError("Hesaplama ifadesi anlaşılamadı")
