function isWhitespace(ch) {
    return /\s/.test(ch);
}

function uniq(values) {
    return Array.from(new Set(values));
}

function lex(query) {
    const tokens = [];
    let i = 0;

    while (i < query.length) {
        const ch = query[i];

        if (isWhitespace(ch)) {
            i++;
            continue;
        }

        if (ch === '(') {
            tokens.push({ type: 'LPAREN' });
            i++;
            continue;
        }

        if (ch === ')') {
            tokens.push({ type: 'RPAREN' });
            i++;
            continue;
        }

        if (ch === '&' && query[i + 1] === '&') {
            tokens.push({ type: 'AND' });
            i += 2;
            continue;
        }

        if (ch === '|' && query[i + 1] === '|') {
            tokens.push({ type: 'OR' });
            i += 2;
            continue;
        }

        const start = i;
        while (i < query.length) {
            const c = query[i];
            const next = query[i + 1];
            if (isWhitespace(c) || c === '(' || c === ')' ||
                (c === '&' && next === '&') || (c === '|' && next === '|')) {
                break;
            }
            i++;
        }

        const raw = query.slice(start, i).trim();
        if (raw) {
            tokens.push({ type: 'TERM', value: raw });
        }
    }

    return tokens;
}

function insertImplicitOr(tokens) {
    const out = [];
    const canEnd = (token) => token && (token.type === 'TERM' || token.type === 'RPAREN');
    const canStart = (token) => token && (token.type === 'TERM' || token.type === 'LPAREN');

    for (const token of tokens) {
        const prev = out[out.length - 1];
        if (canEnd(prev) && canStart(token)) {
            out.push({ type: 'OR' });
        }
        out.push(token);
    }

    return out;
}

function parseTokens(tokens) {
    let pos = 0;

    function peek() {
        return tokens[pos];
    }

    function consume(type) {
        const token = tokens[pos];
        if (!token || token.type !== type) {
            throw new Error(`Consulta DSL inválida: esperado ${type}`);
        }
        pos++;
        return token;
    }

    function parsePrimary() {
        const token = peek();
        if (!token) {
            throw new Error('Consulta DSL inválida: expressão incompleta');
        }

        if (token.type === 'TERM') {
            pos++;
            return { type: 'term', value: token.value };
        }

        if (token.type === 'LPAREN') {
            pos++;
            const expr = parseOr();
            if (!peek() || peek().type !== 'RPAREN') {
                throw new Error('Consulta DSL inválida: parênteses não balanceados');
            }
            pos++;
            return expr;
        }

        if (token.type === 'RPAREN') {
            throw new Error('Consulta DSL inválida: parêntese fechando sem abertura');
        }

        throw new Error('Consulta DSL inválida: token inesperado');
    }

    function parseAnd() {
        let node = parsePrimary();
        while (peek() && peek().type === 'AND') {
            pos++;
            node = { type: 'and', left: node, right: parsePrimary() };
        }
        return node;
    }

    function parseOr() {
        let node = parseAnd();
        while (peek() && peek().type === 'OR') {
            pos++;
            node = { type: 'or', left: node, right: parseAnd() };
        }
        return node;
    }

    const ast = parseOr();
    if (pos !== tokens.length) {
        throw new Error('Consulta DSL inválida: sobra de tokens');
    }
    return ast;
}

function normalizeNode(node, normalizeTerm) {
    if (node.type === 'term') {
        const normalized = uniq(normalizeTerm(node.value)
            .map((value) => String(value).trim())
            .filter(Boolean));
        if (normalized.length === 0) {
            return null;
        }
        return { type: 'term', value: node.value, terms: normalized };
    }

    const left = normalizeNode(node.left, normalizeTerm);
    const right = normalizeNode(node.right, normalizeTerm);
    if (!left || !right) {
        return null;
    }
    return { type: node.type, left, right };
}

function collectTerms(node, out = new Set()) {
    if (!node) {
        return out;
    }

    if (node.type === 'term') {
        for (const term of node.terms) {
            out.add(term);
        }
        return out;
    }

    collectTerms(node.left, out);
    collectTerms(node.right, out);
    return out;
}

function emptySet() {
    return new Set();
}

function setFromIterable(values) {
    if (!values) {
        return emptySet();
    }
    if (values instanceof Set) {
        return values;
    }
    return new Set(values);
}

function union(left, right) {
    const out = new Set(left);
    for (const value of right) {
        out.add(value);
    }
    return out;
}

function intersect(left, right) {
    const out = new Set();
    const [small, large] = left.size <= right.size ? [left, right] : [right, left];
    for (const value of small) {
        if (large.has(value)) {
            out.add(value);
        }
    }
    return out;
}

function evaluateNode(node, lookupTerm) {
    if (node.type === 'term') {
        let out = emptySet();
        for (const term of node.terms) {
            out = union(out, setFromIterable(lookupTerm(term)));
        }
        return out;
    }

    const left = evaluateNode(node.left, lookupTerm);
    const right = evaluateNode(node.right, lookupTerm);
    return node.type === 'and' ? intersect(left, right) : union(left, right);
}

export function containsBooleanSyntax(query) {
    return /&&|\|\||[()]/.test(query);
}

export function parseQueryDsl(query) {
    return parseTokens(insertImplicitOr(lex(query)));
}

export function normalizeQueryDsl(query, normalizeTerm) {
    const ast = normalizeNode(parseQueryDsl(query), normalizeTerm);
    if (!ast) {
        throw new Error('Consulta DSL vazia após normalização');
    }
    return ast;
}

export function collectNormalizedTerms(ast) {
    return uniq(Array.from(collectTerms(ast)));
}

export function evaluateNormalizedDsl(ast, lookupTerm) {
    return evaluateNode(ast, lookupTerm);
}
