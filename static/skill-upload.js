// node_modules/fflate/esm/browser.js
var u8 = Uint8Array;
var u16 = Uint16Array;
var i32 = Int32Array;
var fleb = new u8([
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  1,
  1,
  1,
  1,
  2,
  2,
  2,
  2,
  3,
  3,
  3,
  3,
  4,
  4,
  4,
  4,
  5,
  5,
  5,
  5,
  0,
  /* unused */
  0,
  0,
  /* impossible */
  0
]);
var fdeb = new u8([
  0,
  0,
  0,
  0,
  1,
  1,
  2,
  2,
  3,
  3,
  4,
  4,
  5,
  5,
  6,
  6,
  7,
  7,
  8,
  8,
  9,
  9,
  10,
  10,
  11,
  11,
  12,
  12,
  13,
  13,
  /* unused */
  0,
  0
]);
var clim = new u8([16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]);
var freb = function(eb, start) {
  var b = new u16(31);
  for (var i = 0; i < 31; ++i) {
    b[i] = start += 1 << eb[i - 1];
  }
  var r = new i32(b[30]);
  for (var i = 1; i < 30; ++i) {
    for (var j = b[i]; j < b[i + 1]; ++j) {
      r[j] = j - b[i] << 5 | i;
    }
  }
  return { b, r };
};
var _a = freb(fleb, 2);
var fl = _a.b;
var revfl = _a.r;
fl[28] = 258, revfl[258] = 28;
var _b = freb(fdeb, 0);
var fd = _b.b;
var revfd = _b.r;
var rev = new u16(32768);
for (i = 0; i < 32768; ++i) {
  x = (i & 43690) >> 1 | (i & 21845) << 1;
  x = (x & 52428) >> 2 | (x & 13107) << 2;
  x = (x & 61680) >> 4 | (x & 3855) << 4;
  rev[i] = ((x & 65280) >> 8 | (x & 255) << 8) >> 1;
}
var x;
var i;
var hMap = (function(cd, mb, r) {
  var s = cd.length;
  var i = 0;
  var l = new u16(mb);
  for (; i < s; ++i) {
    if (cd[i])
      ++l[cd[i] - 1];
  }
  var le = new u16(mb);
  for (i = 1; i < mb; ++i) {
    le[i] = le[i - 1] + l[i - 1] << 1;
  }
  var co;
  if (r) {
    co = new u16(1 << mb);
    var rvb = 15 - mb;
    for (i = 0; i < s; ++i) {
      if (cd[i]) {
        var sv = i << 4 | cd[i];
        var r_1 = mb - cd[i];
        var v = le[cd[i] - 1]++ << r_1;
        for (var m = v | (1 << r_1) - 1; v <= m; ++v) {
          co[rev[v] >> rvb] = sv;
        }
      }
    }
  } else {
    co = new u16(s);
    for (i = 0; i < s; ++i) {
      if (cd[i]) {
        co[i] = rev[le[cd[i] - 1]++] >> 15 - cd[i];
      }
    }
  }
  return co;
});
var flt = new u8(288);
for (i = 0; i < 144; ++i)
  flt[i] = 8;
var i;
for (i = 144; i < 256; ++i)
  flt[i] = 9;
var i;
for (i = 256; i < 280; ++i)
  flt[i] = 7;
var i;
for (i = 280; i < 288; ++i)
  flt[i] = 8;
var i;
var fdt = new u8(32);
for (i = 0; i < 32; ++i)
  fdt[i] = 5;
var i;
var flm = /* @__PURE__ */ hMap(flt, 9, 0);
var fdm = /* @__PURE__ */ hMap(fdt, 5, 0);
var shft = function(p) {
  return (p + 7) / 8 | 0;
};
var slc = function(v, s, e) {
  if (s == null || s < 0)
    s = 0;
  if (e == null || e > v.length)
    e = v.length;
  return new u8(v.subarray(s, e));
};
var ec = [
  "unexpected EOF",
  "invalid block type",
  "invalid length/literal",
  "invalid distance",
  "stream finished",
  "no stream handler",
  ,
  // determined by compression function
  "no callback",
  "invalid UTF-8 data",
  "extra field too long",
  "date not in range 1980-2099",
  "filename too long",
  "stream finishing",
  "invalid zip data"
  // determined by unknown compression method
];
var err = function(ind, msg, nt) {
  var e = new Error(msg || ec[ind]);
  e.code = ind;
  if (Error.captureStackTrace)
    Error.captureStackTrace(e, err);
  if (!nt)
    throw e;
  return e;
};
var wbits = function(d, p, v) {
  v <<= p & 7;
  var o = p / 8 | 0;
  d[o] |= v;
  d[o + 1] |= v >> 8;
};
var wbits16 = function(d, p, v) {
  v <<= p & 7;
  var o = p / 8 | 0;
  d[o] |= v;
  d[o + 1] |= v >> 8;
  d[o + 2] |= v >> 16;
};
var hTree = function(d, mb) {
  var t = [];
  for (var i = 0; i < d.length; ++i) {
    if (d[i])
      t.push({ s: i, f: d[i] });
  }
  var s = t.length;
  var t2 = t.slice();
  if (!s)
    return { t: et, l: 0 };
  if (s == 1) {
    var v = new u8(t[0].s + 1);
    v[t[0].s] = 1;
    return { t: v, l: 1 };
  }
  t.sort(function(a, b) {
    return a.f - b.f;
  });
  t.push({ s: -1, f: 25001 });
  var l = t[0], r = t[1], i0 = 0, i1 = 1, i2 = 2;
  t[0] = { s: -1, f: l.f + r.f, l, r };
  while (i1 != s - 1) {
    l = t[t[i0].f < t[i2].f ? i0++ : i2++];
    r = t[i0 != i1 && t[i0].f < t[i2].f ? i0++ : i2++];
    t[i1++] = { s: -1, f: l.f + r.f, l, r };
  }
  var maxSym = t2[0].s;
  for (var i = 1; i < s; ++i) {
    if (t2[i].s > maxSym)
      maxSym = t2[i].s;
  }
  var tr = new u16(maxSym + 1);
  var mbt = ln(t[i1 - 1], tr, 0);
  if (mbt > mb) {
    var i = 0, dt = 0;
    var lft = mbt - mb, cst = 1 << lft;
    t2.sort(function(a, b) {
      return tr[b.s] - tr[a.s] || a.f - b.f;
    });
    for (; i < s; ++i) {
      var i2_1 = t2[i].s;
      if (tr[i2_1] > mb) {
        dt += cst - (1 << mbt - tr[i2_1]);
        tr[i2_1] = mb;
      } else
        break;
    }
    dt >>= lft;
    while (dt > 0) {
      var i2_2 = t2[i].s;
      if (tr[i2_2] < mb)
        dt -= 1 << mb - tr[i2_2]++ - 1;
      else
        ++i;
    }
    for (; i >= 0 && dt; --i) {
      var i2_3 = t2[i].s;
      if (tr[i2_3] == mb) {
        --tr[i2_3];
        ++dt;
      }
    }
    mbt = mb;
  }
  return { t: new u8(tr), l: mbt };
};
var ln = function(n, l, d) {
  return n.s == -1 ? Math.max(ln(n.l, l, d + 1), ln(n.r, l, d + 1)) : l[n.s] = d;
};
var lc = function(c) {
  var s = c.length;
  while (s && !c[--s])
    ;
  var cl = new u16(++s);
  var cli = 0, cln = c[0], cls = 1;
  var w = function(v) {
    cl[cli++] = v;
  };
  for (var i = 1; i <= s; ++i) {
    if (c[i] == cln && i != s)
      ++cls;
    else {
      if (!cln && cls > 2) {
        for (; cls > 138; cls -= 138)
          w(32754);
        if (cls > 2) {
          w(cls > 10 ? cls - 11 << 5 | 28690 : cls - 3 << 5 | 12305);
          cls = 0;
        }
      } else if (cls > 3) {
        w(cln), --cls;
        for (; cls > 6; cls -= 6)
          w(8304);
        if (cls > 2)
          w(cls - 3 << 5 | 8208), cls = 0;
      }
      while (cls--)
        w(cln);
      cls = 1;
      cln = c[i];
    }
  }
  return { c: cl.subarray(0, cli), n: s };
};
var clen = function(cf, cl) {
  var l = 0;
  for (var i = 0; i < cl.length; ++i)
    l += cf[i] * cl[i];
  return l;
};
var wfblk = function(out, pos, dat) {
  var s = dat.length;
  var o = shft(pos + 2);
  out[o] = s & 255;
  out[o + 1] = s >> 8;
  out[o + 2] = out[o] ^ 255;
  out[o + 3] = out[o + 1] ^ 255;
  for (var i = 0; i < s; ++i)
    out[o + i + 4] = dat[i];
  return (o + 4 + s) * 8;
};
var wblk = function(dat, out, final, syms, lf, df, eb, li, bs, bl, p) {
  wbits(out, p++, final);
  ++lf[256];
  var _a2 = hTree(lf, 15), dlt = _a2.t, mlb = _a2.l;
  var _b2 = hTree(df, 15), ddt = _b2.t, mdb = _b2.l;
  var _c = lc(dlt), lclt = _c.c, nlc = _c.n;
  var _d = lc(ddt), lcdt = _d.c, ndc = _d.n;
  var lcfreq = new u16(19);
  for (var i = 0; i < lclt.length; ++i)
    ++lcfreq[lclt[i] & 31];
  for (var i = 0; i < lcdt.length; ++i)
    ++lcfreq[lcdt[i] & 31];
  var _e = hTree(lcfreq, 7), lct = _e.t, mlcb = _e.l;
  var nlcc = 19;
  for (; nlcc > 4 && !lct[clim[nlcc - 1]]; --nlcc)
    ;
  var flen = bl + 5 << 3;
  var ftlen = clen(lf, flt) + clen(df, fdt) + eb;
  var dtlen = clen(lf, dlt) + clen(df, ddt) + eb + 14 + 3 * nlcc + clen(lcfreq, lct) + 2 * lcfreq[16] + 3 * lcfreq[17] + 7 * lcfreq[18];
  if (bs >= 0 && flen <= ftlen && flen <= dtlen)
    return wfblk(out, p, dat.subarray(bs, bs + bl));
  var lm, ll, dm, dl;
  wbits(out, p, 1 + (dtlen < ftlen)), p += 2;
  if (dtlen < ftlen) {
    lm = hMap(dlt, mlb, 0), ll = dlt, dm = hMap(ddt, mdb, 0), dl = ddt;
    var llm = hMap(lct, mlcb, 0);
    wbits(out, p, nlc - 257);
    wbits(out, p + 5, ndc - 1);
    wbits(out, p + 10, nlcc - 4);
    p += 14;
    for (var i = 0; i < nlcc; ++i)
      wbits(out, p + 3 * i, lct[clim[i]]);
    p += 3 * nlcc;
    var lcts = [lclt, lcdt];
    for (var it = 0; it < 2; ++it) {
      var clct = lcts[it];
      for (var i = 0; i < clct.length; ++i) {
        var len = clct[i] & 31;
        wbits(out, p, llm[len]), p += lct[len];
        if (len > 15)
          wbits(out, p, clct[i] >> 5 & 127), p += clct[i] >> 12;
      }
    }
  } else {
    lm = flm, ll = flt, dm = fdm, dl = fdt;
  }
  for (var i = 0; i < li; ++i) {
    var sym = syms[i];
    if (sym > 255) {
      var len = sym >> 18 & 31;
      wbits16(out, p, lm[len + 257]), p += ll[len + 257];
      if (len > 7)
        wbits(out, p, sym >> 23 & 31), p += fleb[len];
      var dst = sym & 31;
      wbits16(out, p, dm[dst]), p += dl[dst];
      if (dst > 3)
        wbits16(out, p, sym >> 5 & 8191), p += fdeb[dst];
    } else {
      wbits16(out, p, lm[sym]), p += ll[sym];
    }
  }
  wbits16(out, p, lm[256]);
  return p + ll[256];
};
var deo = /* @__PURE__ */ new i32([65540, 131080, 131088, 131104, 262176, 1048704, 1048832, 2114560, 2117632]);
var et = /* @__PURE__ */ new u8(0);
var dflt = function(dat, lvl, plvl, pre, post, st) {
  var s = st.z || dat.length;
  var o = new u8(pre + s + 5 * (1 + Math.ceil(s / 7e3)) + post);
  var w = o.subarray(pre, o.length - post);
  var lst = st.l;
  var pos = (st.r || 0) & 7;
  if (lvl) {
    if (pos)
      w[0] = st.r >> 3;
    var opt = deo[lvl - 1];
    var n = opt >> 13, c = opt & 8191;
    var msk_1 = (1 << plvl) - 1;
    var prev = st.p || new u16(32768), head = st.h || new u16(msk_1 + 1);
    var bs1_1 = Math.ceil(plvl / 3), bs2_1 = 2 * bs1_1;
    var hsh = function(i2) {
      return (dat[i2] ^ dat[i2 + 1] << bs1_1 ^ dat[i2 + 2] << bs2_1) & msk_1;
    };
    var syms = new i32(25e3);
    var lf = new u16(288), df = new u16(32);
    var lc_1 = 0, eb = 0, i = st.i || 0, li = 0, wi = st.w || 0, bs = 0;
    for (; i + 2 < s; ++i) {
      var hv = hsh(i);
      var imod = i & 32767, pimod = head[hv];
      prev[imod] = pimod;
      head[hv] = imod;
      if (wi <= i) {
        var rem = s - i;
        if ((lc_1 > 7e3 || li > 24576) && (rem > 423 || !lst)) {
          pos = wblk(dat, w, 0, syms, lf, df, eb, li, bs, i - bs, pos);
          li = lc_1 = eb = 0, bs = i;
          for (var j = 0; j < 286; ++j)
            lf[j] = 0;
          for (var j = 0; j < 30; ++j)
            df[j] = 0;
        }
        var l = 2, d = 0, ch_1 = c, dif = imod - pimod & 32767;
        if (rem > 2 && hv == hsh(i - dif)) {
          var maxn = Math.min(n, rem) - 1;
          var maxd = Math.min(32767, i);
          var ml = Math.min(258, rem);
          while (dif <= maxd && --ch_1 && imod != pimod) {
            if (dat[i + l] == dat[i + l - dif]) {
              var nl = 0;
              for (; nl < ml && dat[i + nl] == dat[i + nl - dif]; ++nl)
                ;
              if (nl > l) {
                l = nl, d = dif;
                if (nl > maxn)
                  break;
                var mmd = Math.min(dif, nl - 2);
                var md = 0;
                for (var j = 0; j < mmd; ++j) {
                  var ti = i - dif + j & 32767;
                  var pti = prev[ti];
                  var cd = ti - pti & 32767;
                  if (cd > md)
                    md = cd, pimod = ti;
                }
              }
            }
            imod = pimod, pimod = prev[imod];
            dif += imod - pimod & 32767;
          }
        }
        if (d) {
          syms[li++] = 268435456 | revfl[l] << 18 | revfd[d];
          var lin = revfl[l] & 31, din = revfd[d] & 31;
          eb += fleb[lin] + fdeb[din];
          ++lf[257 + lin];
          ++df[din];
          wi = i + l;
          ++lc_1;
        } else {
          syms[li++] = dat[i];
          ++lf[dat[i]];
        }
      }
    }
    for (i = Math.max(i, wi); i < s; ++i) {
      syms[li++] = dat[i];
      ++lf[dat[i]];
    }
    pos = wblk(dat, w, lst, syms, lf, df, eb, li, bs, i - bs, pos);
    if (!lst) {
      st.r = pos & 7 | w[pos / 8 | 0] << 3;
      pos -= 7;
      st.h = head, st.p = prev, st.i = i, st.w = wi;
    }
  } else {
    for (var i = st.w || 0; i < s + lst; i += 65535) {
      var e = i + 65535;
      if (e >= s) {
        w[pos / 8 | 0] = lst;
        e = s;
      }
      pos = wfblk(w, pos + 1, dat.subarray(i, e));
    }
    st.i = s;
  }
  return slc(o, 0, pre + shft(pos) + post);
};
var crct = /* @__PURE__ */ (function() {
  var t = new Int32Array(256);
  for (var i = 0; i < 256; ++i) {
    var c = i, k = 9;
    while (--k)
      c = (c & 1 && -306674912) ^ c >>> 1;
    t[i] = c;
  }
  return t;
})();
var crc = function() {
  var c = -1;
  return {
    p: function(d) {
      var cr = c;
      for (var i = 0; i < d.length; ++i)
        cr = crct[cr & 255 ^ d[i]] ^ cr >>> 8;
      c = cr;
    },
    d: function() {
      return ~c;
    }
  };
};
var dopt = function(dat, opt, pre, post, st) {
  if (!st) {
    st = { l: 1 };
    if (opt.dictionary) {
      var dict = opt.dictionary.subarray(-32768);
      var newDat = new u8(dict.length + dat.length);
      newDat.set(dict);
      newDat.set(dat, dict.length);
      dat = newDat;
      st.w = dict.length;
    }
  }
  return dflt(dat, opt.level == null ? 6 : opt.level, opt.mem == null ? st.l ? Math.ceil(Math.max(8, Math.min(13, Math.log(dat.length))) * 1.5) : 20 : 12 + opt.mem, pre, post, st);
};
var mrg = function(a, b) {
  var o = {};
  for (var k in a)
    o[k] = a[k];
  for (var k in b)
    o[k] = b[k];
  return o;
};
var wbytes = function(d, b, v) {
  for (; v; ++b)
    d[b] = v, v >>>= 8;
};
function deflateSync(data, opts) {
  return dopt(data, opts || {}, 0, 0);
}
var fltn = function(d, p, t, o) {
  for (var k in d) {
    var val = d[k], n = p + k, op = o;
    if (Array.isArray(val))
      op = mrg(o, val[1]), val = val[0];
    if (ArrayBuffer.isView(val))
      t[n] = [val, op];
    else {
      t[n += "/"] = [new u8(0), op];
      fltn(val, n, t, o);
    }
  }
};
var te = typeof TextEncoder != "undefined" && /* @__PURE__ */ new TextEncoder();
var td = typeof TextDecoder != "undefined" && /* @__PURE__ */ new TextDecoder();
var tds = 0;
try {
  td.decode(et, { stream: true });
  tds = 1;
} catch (e) {
}
function strToU8(str, latin1) {
  if (latin1) {
    var ar_1 = new u8(str.length);
    for (var i = 0; i < str.length; ++i)
      ar_1[i] = str.charCodeAt(i);
    return ar_1;
  }
  if (te)
    return te.encode(str);
  var l = str.length;
  var ar = new u8(str.length + (str.length >> 1));
  var ai = 0;
  var w = function(v) {
    ar[ai++] = v;
  };
  for (var i = 0; i < l; ++i) {
    if (ai + 5 > ar.length) {
      var n = new u8(ai + 8 + (l - i << 1));
      n.set(ar);
      ar = n;
    }
    var c = str.charCodeAt(i);
    if (c < 128 || latin1)
      w(c);
    else if (c < 2048)
      w(192 | c >> 6), w(128 | c & 63);
    else if (c > 55295 && c < 57344)
      c = 65536 + (c & 1023 << 10) | str.charCodeAt(++i) & 1023, w(240 | c >> 18), w(128 | c >> 12 & 63), w(128 | c >> 6 & 63), w(128 | c & 63);
    else
      w(224 | c >> 12), w(128 | c >> 6 & 63), w(128 | c & 63);
  }
  return slc(ar, 0, ai);
}
var exfl = function(ex) {
  var le = 0;
  if (ex) {
    for (var k in ex) {
      var l = ex[k].length;
      if (l > 65535)
        err(9);
      le += l + 4;
    }
  }
  return le;
};
var wzh = function(d, b, f, fn, u, c, ce, co) {
  var fl2 = fn.length, ex = f.extra, col = co && co.length;
  var exl = exfl(ex);
  wbytes(d, b, ce != null ? 33639248 : 67324752), b += 4;
  if (ce != null)
    d[b++] = 20, d[b++] = f.os;
  d[b] = 20, b += 2;
  d[b++] = f.flag << 1 | (c < 0 && 8), d[b++] = u && 8;
  d[b++] = f.compression & 255, d[b++] = f.compression >> 8;
  var dt = new Date(f.mtime == null ? Date.now() : f.mtime), y = dt.getFullYear() - 1980;
  if (y < 0 || y > 119)
    err(10);
  wbytes(d, b, y << 25 | dt.getMonth() + 1 << 21 | dt.getDate() << 16 | dt.getHours() << 11 | dt.getMinutes() << 5 | dt.getSeconds() >> 1), b += 4;
  if (c != -1) {
    wbytes(d, b, f.crc);
    wbytes(d, b + 4, c < 0 ? -c - 2 : c);
    wbytes(d, b + 8, f.size);
  }
  wbytes(d, b + 12, fl2);
  wbytes(d, b + 14, exl), b += 16;
  if (ce != null) {
    wbytes(d, b, col);
    wbytes(d, b + 6, f.attrs);
    wbytes(d, b + 10, ce), b += 14;
  }
  d.set(fn, b);
  b += fl2;
  if (exl) {
    for (var k in ex) {
      var exf = ex[k], l = exf.length;
      wbytes(d, b, +k);
      wbytes(d, b + 2, l);
      d.set(exf, b + 4), b += 4 + l;
    }
  }
  if (col)
    d.set(co, b), b += col;
  return b;
};
var wzf = function(o, b, c, d, e) {
  wbytes(o, b, 101010256);
  wbytes(o, b + 8, c);
  wbytes(o, b + 10, c);
  wbytes(o, b + 12, d);
  wbytes(o, b + 16, e);
};
function zipSync(data, opts) {
  if (!opts)
    opts = {};
  var r = {};
  var files = [];
  fltn(data, "", r, opts);
  var o = 0;
  var tot = 0;
  for (var fn in r) {
    var _a2 = r[fn], file = _a2[0], p = _a2[1];
    var compression = p.level == 0 ? 0 : 8;
    var f = strToU8(fn), s = f.length;
    var com = p.comment, m = com && strToU8(com), ms = m && m.length;
    var exl = exfl(p.extra);
    if (s > 65535)
      err(11);
    var d = compression ? deflateSync(file, p) : file, l = d.length;
    var c = crc();
    c.p(file);
    files.push(mrg(p, {
      size: file.length,
      crc: c.d(),
      c: d,
      f,
      m,
      u: s != fn.length || m && com.length != ms,
      o,
      compression
    }));
    o += 30 + s + exl + l;
    tot += 76 + 2 * (s + exl) + (ms || 0) + l;
  }
  var out = new u8(tot + 22), oe = o, cdl = tot - o;
  for (var i = 0; i < files.length; ++i) {
    var f = files[i];
    wzh(out, f.o, f, f.f, f.u, f.c.length);
    var badd = 30 + f.f.length + exfl(f.extra);
    out.set(f.c, f.o + badd);
    wzh(out, o, f, f.f, f.u, f.c.length, f.o, f.m), o += 16 + badd + (f.m ? f.m.length : 0);
  }
  wzf(out, o, files.length, cdl, oe);
  return out;
}

// src/skill-package.js
var LIMITS = Object.freeze({
  files: 512,
  total: 16 * 1024 * 1024,
  file: 10 * 1024 * 1024,
  zip: 20 * 1024 * 1024,
  scanned: 1e4,
  depth: 32
});
var ignoredDirectories = /* @__PURE__ */ new Set([
  ".git",
  ".svn",
  ".hg",
  "node_modules",
  "__pycache__",
  ".venv",
  "venv",
  ".dart_tool",
  ".idea"
]);
function safePath(path) {
  if (typeof path !== "string" || !path || /[\\\x00-\x1f:]/.test(path) || path.split("/").some((p) => !p || p === "." || p === "..") || path.length > 1024 || path.split("/").length > LIMITS.depth) throw new Error("\u6587\u4EF6\u8DEF\u5F84\u65E0\u6548\u6216\u5C42\u7EA7\u8FC7\u6DF1\u3002");
  return path;
}
function excluded(path) {
  const parts = path.toLowerCase().split("/");
  const name = parts.at(-1);
  return parts.some((p) => ignoredDirectories.has(p)) || name === ".ds_store" || name.endsWith(".pyc") || name === ".env" || name.startsWith(".env.") && ![".env.example", ".env.sample", ".env.template"].includes(name) || ["id_rsa", "id_ed25519", "id_ecdsa", ".npmrc", ".pypirc"].includes(name) || /\.(pem|key|p12|pfx)$/.test(name);
}
function reviewFolder(records, skipped = []) {
  if (!records.length) throw new Error("\u6587\u4EF6\u5939\u4E3A\u7A7A\u6216\u6CA1\u6709\u53EF\u4E0A\u4F20\u6587\u4EF6\u3002");
  if (records.length > LIMITS.scanned) throw new Error("\u6587\u4EF6\u5939\u6587\u4EF6\u8FC7\u591A\uFF0C\u8BF7\u53EA\u9009\u62E9 Skill \u6839\u76EE\u5F55\u3002");
  const roots = /* @__PURE__ */ new Set();
  const paths = /* @__PURE__ */ new Set();
  const files = [];
  const omitted = [...skipped];
  let total = 0;
  for (const record of records) {
    const path = safePath(record.path);
    const parts = path.split("/");
    if (parts.length < 2) throw new Error("\u8BF7\u9009\u62E9\u5305\u542B SKILL.md \u7684\u6839\u6587\u4EF6\u5939\uFF0C\u800C\u4E0D\u662F\u591A\u4E2A\u6563\u6587\u4EF6\u3002");
    roots.add(parts[0]);
    if (paths.has(path)) throw new Error("\u6587\u4EF6\u5939\u5305\u542B\u91CD\u590D\u8DEF\u5F84\u3002");
    paths.add(path);
    if (excluded(parts.slice(1).join("/"))) {
      omitted.push(path);
      continue;
    }
    if (!Number.isSafeInteger(record.file.size) || record.file.size < 0 || record.file.size > LIMITS.file)
      throw new Error("\u5355\u4E2A\u6587\u4EF6\u4E0D\u80FD\u8D85\u8FC7 10 MiB\u3002");
    total += record.file.size;
    files.push(record);
    if (total > LIMITS.total || files.length > LIMITS.files)
      throw new Error("\u6587\u4EF6\u5939\u6700\u591A\u5305\u542B 512 \u4E2A\u6587\u4EF6\u3001\u603B\u8BA1 16 MiB\uFF0C\u8BF7\u79FB\u9664\u65E0\u5173\u5185\u5BB9\u3002");
  }
  if (roots.size !== 1) throw new Error("\u6BCF\u6B21\u53EA\u80FD\u4E0A\u4F20\u4E00\u4E2A Skill \u6839\u6587\u4EF6\u5939\u3002");
  const root = [...roots][0];
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(root) || root.length > 64)
    throw new Error("\u6839\u6587\u4EF6\u5939\u540D\u987B\u4E3A\u5C0F\u5199\u5B57\u6BCD\u3001\u6570\u5B57\u6216\u8FDE\u5B57\u7B26\uFF0C\u5E76\u4E0E SKILL.md \u7684 name \u4E00\u81F4\u3002");
  if (!files.some((f) => f.path === `${root}/SKILL.md`))
    throw new Error("\u6839\u6587\u4EF6\u5939\u5FC5\u987B\u76F4\u63A5\u5305\u542B SKILL.md\uFF0C\u8BF7\u4E0D\u8981\u9009\u62E9\u5916\u5C42\u76EE\u5F55\u3002");
  files.sort((a, b) => a.path.localeCompare(b.path));
  return { root, files, omitted, total };
}
async function collectDirectory(entry, signal) {
  const records = [], skipped = [];
  let scanned = 0;
  async function visit(item, parent = "") {
    signal?.throwIfAborted();
    if (++scanned > LIMITS.scanned) throw new Error("\u626B\u63CF\u6761\u76EE\u8FC7\u591A\uFF0C\u8BF7\u53EA\u9009\u62E9 Skill \u6839\u76EE\u5F55\u3002");
    if (item.name.includes("/")) throw new Error("\u6587\u4EF6\u540D\u5305\u542B\u975E\u6CD5\u8DEF\u5F84\u3002");
    const path = safePath(parent ? `${parent}/${item.name}` : item.name);
    if (parent && excluded(path.split("/").slice(1).join("/"))) {
      skipped.push(path + (item.isDirectory ? "/" : ""));
      return;
    }
    if (item.isFile) {
      const file = await new Promise((resolve, reject) => item.file(resolve, reject));
      records.push({ path, file });
      if (records.length > LIMITS.files) throw new Error("\u6587\u4EF6\u5939\u6700\u591A\u5305\u542B 512 \u4E2A\u6587\u4EF6\u3002");
    } else if (item.isDirectory) {
      const reader = item.createReader();
      for (; ; ) {
        signal?.throwIfAborted();
        const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
        if (!batch.length) break;
        for (const child of batch) await visit(child, path);
      }
    } else throw new Error("\u4E0D\u652F\u6301\u7684\u6587\u4EF6\u6761\u76EE\uFF0C\u8BF7\u6539\u7528 ZIP\u3002");
  }
  await visit(entry);
  return { records, skipped };
}
async function packageFolder(records, skipped, progress, signal) {
  const review = reviewFolder(records, skipped);
  const content = /* @__PURE__ */ Object.create(null);
  let count = 0;
  for (const { path, file } of review.files) {
    signal?.throwIfAborted();
    progress(`\u6B63\u5728\u8BFB\u53D6\u6587\u4EF6 ${++count}/${review.files.length}`);
    const bytes2 = new Uint8Array(await file.arrayBuffer());
    signal?.throwIfAborted();
    if (bytes2.length !== file.size) throw new Error("\u6587\u4EF6\u8BFB\u53D6\u4E0D\u5B8C\u6574\uFF0C\u8BF7\u91CD\u65B0\u9009\u62E9\u3002");
    content[path] = bytes2;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  progress("\u6B63\u5728\u672C\u5730\u6253\u5305\u2026");
  await new Promise((resolve) => setTimeout(resolve, 0));
  signal?.throwIfAborted();
  const bytes = zipSync(content, { level: 0, mtime: new Date(2e3, 0, 1) });
  if (bytes.length > LIMITS.zip) throw new Error("\u6253\u5305\u7ED3\u679C\u8D85\u8FC7 ZIP \u5927\u5C0F\u9650\u5236\u3002");
  return { bytes, info: {
    filename: `${review.root}.zip`,
    folder: true,
    files: review.files.map((f) => ({ path: f.path, size: f.file.size })),
    excluded: review.omitted,
    total: review.total
  } };
}
async function readZip(file, signal) {
  if (!file.name.toLowerCase().endsWith(".zip")) throw new Error("\u8BF7\u63D0\u4F9B\u4E00\u4E2A Skill \u6587\u4EF6\u5939\u6216 ZIP \u6587\u4EF6\u3002");
  if (!file.size || file.size > LIMITS.zip) throw new Error("ZIP \u4E0D\u80FD\u4E3A\u7A7A\u6216\u8D85\u8FC7 20 MiB\u3002");
  signal?.throwIfAborted();
  const bytes = new Uint8Array(await file.arrayBuffer());
  signal?.throwIfAborted();
  if (bytes.length !== file.size || bytes[0] !== 80 || bytes[1] !== 75)
    throw new Error("\u6587\u4EF6\u4E0D\u662F\u6709\u6548 ZIP\uFF0C\u8BF7\u52FF\u4EC5\u4FEE\u6539\u6269\u5C55\u540D\u3002");
  return { bytes, info: { filename: file.name, folder: false, files: [], excluded: [], total: file.size } };
}
function droppedSource(data) {
  const items = Array.from(data.items ?? []).filter((item2) => item2.kind === "file");
  if (items.length !== 1) throw new Error("\u6BCF\u6B21\u8BF7\u62D6\u5165\u4E00\u4E2A\u6587\u4EF6\u5939\u6216\u4E00\u4E2A ZIP\uFF0C\u4E0D\u652F\u6301\u591A\u4E2A\u6DF7\u5408\u6761\u76EE\u3002");
  const item = items[0];
  const entry = item.getAsEntry?.() ?? item.webkitGetAsEntry?.();
  if (entry?.isDirectory) return { entry };
  const file = item.getAsFile();
  if (!file) throw new Error("\u6D4F\u89C8\u5668\u65E0\u6CD5\u8BFB\u53D6\u6B64\u76EE\u5F55\uFF0C\u8BF7\u4F7F\u7528\u201C\u9009\u62E9\u6587\u4EF6\u5939\u201D\u6216 ZIP\u3002");
  return { file };
}

// src/skill-upload.js
function mount(container, theme, onFile, onState) {
  const root = container.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
    :host{display:block;font:14px system-ui;color:var(--text)}
    .box{box-sizing:border-box;border:1px dashed var(--border);border-radius:12px;
      background:var(--bg);padding:16px;text-align:center;min-height:156px}
    .drag{border-color:#22c55e;background:var(--hover)}
    button{font:inherit;color:var(--text);background:var(--bg);border:1px solid var(--border);
      border-radius:8px;padding:8px 14px;cursor:pointer}
    button:focus-visible{outline:2px solid #22c55e;outline-offset:3px}
    button:disabled{opacity:.5;cursor:default}p{margin:0 0 10px;line-height:1.5}
    small{display:block;color:var(--muted);margin-top:10px}
    .choices{display:flex;justify-content:center;gap:8px;margin-top:8px;flex-wrap:wrap}
    [hidden]{display:none!important}
  </style><section class="box" aria-label="\u6DFB\u52A0 Skill\uFF1A\u62D6\u5165\u6587\u4EF6\u5939\u6216 ZIP">
    <p><strong>\u62D6\u5165\u4E00\u4E2A Skill \u6587\u4EF6\u5939\u6216 ZIP</strong></p>
    <button type="button" class="choose" aria-expanded="false">\u9009\u62E9\u6587\u4EF6\u2026</button>
    <div class="choices" hidden><button type="button" class="folder">\u9009\u62E9\u6587\u4EF6\u5939</button>
      <button type="button" class="zip">\u9009\u62E9 ZIP \u6587\u4EF6</button></div>
    <small role="status" aria-live="polite">\u6587\u4EF6\u5939\u5728\u672C\u5730\u6253\u5305\uFF0C\u4E0D\u6267\u884C\u6587\u4EF6\uFF1B\u786E\u8BA4\u4E0A\u4F20\u524D\u4E0D\u4F1A\u53D1\u9001\u3002</small>
  </section>`;
  const box = root.querySelector(".box"), status = root.querySelector("[role=status]");
  const choose = root.querySelector(".choose"), choices = root.querySelector(".choices");
  const folder = root.querySelector(".folder"), zip = root.querySelector(".zip");
  const events = new AbortController();
  let operation, input, disposed = false, locked = false, busy = false;
  const setTheme = (value) => {
    const dark = value === "dark";
    const colors = dark ? ["#e2e8f0", "#0f172a", "#475569", "#94a3b8", "#14382f"] : ["#172033", "#f8fafc", "#94a3b8", "#475569", "#e5f5ec"];
    ["text", "bg", "border", "muted", "hover"].forEach((name, i) => container.style.setProperty(`--${name}`, colors[i]));
  };
  setTheme(theme);
  function controls() {
    for (const button of [choose, folder, zip]) button.disabled = locked || busy;
  }
  function state(text, working) {
    if (disposed) return;
    status.textContent = text;
    busy = working;
    controls();
    onState(text, working);
  }
  function cleanupInput() {
    input?.remove();
    input = void 0;
  }
  async function process(source) {
    if (disposed || locked || busy) return;
    operation?.abort();
    operation = new AbortController();
    const signal = operation.signal;
    choices.hidden = true;
    choose.setAttribute("aria-expanded", "false");
    state("\u6B63\u5728\u68C0\u67E5\u9009\u62E9\u5185\u5BB9\u2026", true);
    try {
      let result;
      if (source.entry || source.records) {
        const data = source.records ? source : await collectDirectory(source.entry, signal);
        result = await packageFolder(
          data.records,
          data.skipped ?? [],
          (text) => state(text, true),
          signal
        );
      } else result = await readZip(source.file, signal);
      signal.throwIfAborted();
      if (!disposed) onFile(JSON.stringify(result.info), result.bytes);
      state("\u5DF2\u5728\u672C\u5730\u51C6\u5907\u597D\uFF0C\u8BF7\u68C0\u67E5\u6E05\u5355\u540E\u4E0A\u4F20\u3002", false);
    } catch (error) {
      if (signal.aborted || disposed) return;
      state(`\u9009\u62E9\u5931\u8D25\uFF1A${error.message || "\u65E0\u6CD5\u8BFB\u53D6\uFF0C\u8BF7\u91CD\u8BD5\u6216\u6539\u7528 ZIP\u3002"}`, false);
    }
  }
  function pick(directory) {
    if (locked || busy || disposed) return;
    cleanupInput();
    const selectedInput = document.createElement("input");
    selectedInput.type = "file";
    selectedInput.hidden = true;
    if (directory) {
      if (!("webkitdirectory" in selectedInput)) {
        state("\u6B64\u6D4F\u89C8\u5668\u4E0D\u652F\u6301\u6587\u4EF6\u5939\u9009\u62E9\uFF0C\u8BF7\u6539\u7528 ZIP\u3002", false);
        return;
      }
      selectedInput.webkitdirectory = true;
    } else selectedInput.accept = ".zip,application/zip";
    input = selectedInput;
    container.appendChild(input);
    input.addEventListener("cancel", () => {
      if (input !== selectedInput || disposed) return;
      cleanupInput();
      state("\u5DF2\u53D6\u6D88\u9009\u62E9\uFF0C\u53EF\u91CD\u65B0\u9009\u62E9\u3002", false);
    }, { once: true });
    input.addEventListener("change", () => {
      if (input !== selectedInput || disposed) return;
      const files = Array.from(selectedInput.files ?? []);
      cleanupInput();
      if (!files.length) {
        state("\u5DF2\u53D6\u6D88\u9009\u62E9\uFF0C\u53EF\u91CD\u65B0\u9009\u62E9\u3002", false);
        return;
      }
      if (directory) process({ records: files.map((file) => ({ path: file.webkitRelativePath, file })) });
      else if (files.length === 1) process({ file: files[0] });
      else state("\u6BCF\u6B21\u53EA\u80FD\u9009\u62E9\u4E00\u4E2A ZIP\u3002", false);
    }, { once: true });
    input.click();
  }
  choose.onclick = () => {
    choices.hidden = !choices.hidden;
    choose.setAttribute("aria-expanded", String(!choices.hidden));
  };
  folder.onclick = () => pick(true);
  zip.onclick = () => pick(false);
  box.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (!locked && !busy) box.classList.add("drag");
  });
  box.addEventListener("dragleave", () => box.classList.remove("drag"));
  box.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    box.classList.remove("drag");
    if (locked || busy || disposed) return;
    try {
      process(droppedSource(event.dataTransfer));
    } catch (error) {
      state(`\u9009\u62E9\u5931\u8D25\uFF1A${error.message}`, false);
    }
  });
  for (const name of ["dragover", "drop"]) document.addEventListener(name, (event) => {
    if (Array.from(event.dataTransfer?.types ?? []).includes("Files")) event.preventDefault();
  }, { signal: events.signal });
  return {
    setTheme,
    setLocked(value) {
      locked = value;
      controls();
    },
    close() {
      disposed = true;
      operation?.abort();
      events.abort();
      cleanupInput();
      root.replaceChildren();
    }
  };
}
globalThis.mcpcatSkillUpload = { mount };
export {
  mount
};
