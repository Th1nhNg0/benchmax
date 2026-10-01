Let $d_n(x)$ be the $n$-th decimal digit of the fractional part of $x$, or $0$ if the fractional part has fewer than $n$ digits.

For example:

- $d_7(1) = d_7(\frac{1}{2}) = d_7(\frac{1}{4}) = d_7(\frac{1}{5}) = 0$
- $d_7(\frac{1}{3}) = 3$ since $\frac{1}{3} = 0.333333\,[3]\,333\ldots$
- $d_7(\frac{1}{6}) = 6$ since $\frac{1}{6} = 0.166666\,[6]\,666\ldots$
- $d_7(\frac{1}{7}) = 1$ since $\frac{1}{7} = 0.142857\,[1]\,428\ldots$

(The digit in brackets is the 7th digit.)

Let $S(n) = \sum_{k=1}^{n} d_n\left(\frac{1}{k}\right)$.

You are given:

- $S(7) = 0 + 0 + 3 + 0 + 0 + 6 + 1 = 10$
- $S(100) = 418$

Find $S(10^7)$.

End your reply with a single line of the form: `ANSWER: <number>`
