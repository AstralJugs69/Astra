"""Curated 15 Hardest Reasoning Benchmark Tasks from MR-Ben and ProcessBench.

Covers:
- 5 Hardest Software & Coding Meta-Reasoning Tasks (MR-Ben Coding)
- 5 Hardest Logic & Constraint Reasoning Tasks (MR-Ben Logic)
- 5 Hardest Olympiad Mathematical Process Tasks (ProcessBench / MR-Ben Math)
"""

from typing import Dict, List
from astra.evaluation.reasoning_evaluator import ReasoningTaskItem

HARD_REASONING_TASKS: Dict[str, ReasoningTaskItem] = {
    # =========================================================================
    # 💻 CODING META-REASONING (5 Tasks)
    # =========================================================================
    "mrben-coding-01": ReasoningTaskItem(
        task_id="mrben-coding-01",
        benchmark="MR-Ben",
        subject="coding",
        question="Implement an asynchronous LRU cache with TTL eviction where expired entries are cleaned up lazily on lookup or asynchronously in background without deadlocking coroutines.",
        solution_steps=[
            "Step 0: Define AsyncLRUCache class with an internal OrderedDict mapping keys to (value, expiry_timestamp).",
            "Step 1: In `async def get(self, key)`: Check if key exists; if expired, delete key from dict and return None.",
            "Step 2: If key is valid, call `self._cache.move_to_end(key)` to mark as recently used under asyncio.Lock().",
            "Step 3: In `async def put(self, key, val, ttl)`: Acquire asyncio.Lock(); if len >= capacity, call `self._cache.popitem(last=True)` to evict the least recently used entry.",
            "Step 4: Store key with calculated expiry timestamp `time.time() + ttl` and release lock.",
        ],
        ground_truth_correctness="incorrect",
        ground_truth_first_error_step=3,
        ground_truth_error_reason="In Step 3, calling popitem(last=True) removes the most recently used item instead of the least recently used item (which requires last=False in OrderedDict).",
        ground_truth_answer="Use self._cache.popitem(last=False) to evict the oldest entry.",
    ),
    "mrben-coding-02": ReasoningTaskItem(
        task_id="mrben-coding-02",
        benchmark="MR-Ben",
        subject="coding",
        question="Find the maximum subarray sum in a circular integer array of size N in O(N) time and O(1) space.",
        solution_steps=[
            "Step 0: Compute the maximum standard contiguous subarray sum using Kadane's algorithm `max_kadane`.",
            "Step 1: Compute the total sum of all elements in the array `total_sum`.",
            "Step 2: Invert the sign of all elements in the array and run Kadane's algorithm to find the minimum subarray sum: `min_kadane = -kadane(inverted_arr)`.",
            "Step 3: The maximum circular subarray sum is given by `max(max_kadane, total_sum - min_kadane)`.",
            "Step 4: Return this maximum as the final answer.",
        ],
        ground_truth_correctness="incorrect",
        ground_truth_first_error_step=3,
        ground_truth_error_reason="When all elements in the array are negative, total_sum equals min_kadane, so total_sum - min_kadane evaluates to 0 (an empty subarray), which is invalid since the subarray must be non-empty.",
        ground_truth_answer="If max_kadane < 0, return max_kadane directly without subtracting min_kadane.",
    ),
    "mrben-coding-03": ReasoningTaskItem(
        task_id="mrben-coding-03",
        benchmark="MR-Ben",
        subject="coding",
        question="Design a thread-safe Singleton in Python that supports lazy initialization without double-checked locking race conditions.",
        solution_steps=[
            "Step 0: Use a metaclass `SingletonMeta(type)` with an internal `_instances = {}` dictionary.",
            "Step 1: Define a class-level `threading.Lock()` inside `SingletonMeta` to synchronize instantiation.",
            "Step 2: In `__call__(cls, *args, **kwargs)`: Check if `cls not in cls._instances` without holding lock.",
            "Step 3: If not present, acquire `cls._lock`, create instance `instance = super().__call__(*args, **kwargs)`, store in `cls._instances[cls] = instance`, and return.",
        ],
        ground_truth_correctness="incorrect",
        ground_truth_first_error_step=3,
        ground_truth_error_reason="Step 3 fails to re-check `cls not in cls._instances` after acquiring the lock (violating the double-checked locking pattern), allowing two competing threads that passed Step 2 to both create instances.",
        ground_truth_answer="Add second check `if cls not in cls._instances:` inside the lock critical section.",
    ),
    "mrben-coding-04": ReasoningTaskItem(
        task_id="mrben-coding-04",
        benchmark="MR-Ben",
        subject="coding",
        question="Serialize and deserialize a binary tree using preorder traversal string representation with '#' for null nodes.",
        solution_steps=[
            "Step 0: In serialize: traverse tree recursively in preorder: visit node.val, then serialize(node.left), then serialize(node.right), appending ',' delimiters and '#' for None.",
            "Step 1: Join tokens into a single comma-delimited string.",
            "Step 2: In deserialize: split string by ',' into a list iterator or queue.",
            "Step 3: Define recursive helper `build()`: pop next token; if token == '#', return None.",
            "Step 4: Create `node = TreeNode(int(token))`; set `node.left = build()`; set `node.right = build()`; return node.",
        ],
        ground_truth_correctness="correct",
        ground_truth_first_error_step=-1,
        ground_truth_error_reason="No error. Preorder serialization with null markers is uniquely reconstructible.",
        ground_truth_answer="Serialized string correctly reconstructs identical binary tree hierarchy.",
    ),
    "mrben-coding-05": ReasoningTaskItem(
        task_id="mrben-coding-05",
        benchmark="MR-Ben",
        subject="coding",
        question="Perform topological sort on a directed graph with N vertices and detect if a cycle exists using Kahn's algorithm (indegree-based BFS).",
        solution_steps=[
            "Step 0: Compute in-degree for every vertex in the graph.",
            "Step 1: Initialize queue `Q` with all vertices having in-degree == 0.",
            "Step 2: Initialize empty list `order = []`.",
            "Step 3: While `Q` is non-empty: pop vertex `u`, append `u` to `order`, and for each neighbor `v` of `u`, decrement `in_degree[v]`.",
            "Step 4: If `in_degree[v] <= 0`, push `v` to `Q`.",
            "Step 5: If `len(order) == N`, return `order`; else return cycle detected.",
        ],
        ground_truth_correctness="incorrect",
        ground_truth_first_error_step=4,
        ground_truth_error_reason="In Step 4, checking `in_degree[v] <= 0` pushes vertex v to the queue multiple times if in-degree drops below 0. It must strictly check `in_degree[v] == 0`.",
        ground_truth_answer="Check `if in_degree[v] == 0:` before pushing to queue.",
    ),

    # =========================================================================
    # 🧩 LOGIC & CONSTRAINT META-REASONING (5 Tasks)
    # =========================================================================
    "mrben-logic-01": ReasoningTaskItem(
        task_id="mrben-logic-01",
        benchmark="MR-Ben",
        subject="logic",
        question="Three people A, B, and C are either Knights (always tell truth) or Knaves (always lie). A says: 'At least one of us is a Knave.' B says: 'A is a Knave.' Determine the identities of A, B, and C.",
        solution_steps=[
            "Step 0: Suppose A is a Knave (liar). Then A's statement 'At least one of us is a Knave' must be false.",
            "Step 1: If that statement is false, then NO ONE is a Knave, meaning all of A, B, and C are Knights.",
            "Step 2: But this contradicts our assumption that A is a Knave. Therefore, A cannot be a Knave; A must be a Knight.",
            "Step 3: Since A is a Knight, A's statement is true: at least one of A, B, C is a Knave.",
            "Step 4: B says 'A is a Knave'. Since we know A is a Knight, B's statement is false, so B must be a Knave.",
            "Step 5: Since B is a Knave, the condition 'at least one is a Knave' is satisfied regardless of C. Therefore C can be either a Knight or a Knave.",
        ],
        ground_truth_correctness="correct",
        ground_truth_first_error_step=-1,
        ground_truth_error_reason="No error. The deduction is sound and valid.",
        ground_truth_answer="A is a Knight, B is a Knave, and C's identity cannot be uniquely determined from the statements alone.",
    ),
    "mrben-logic-02": ReasoningTaskItem(
        task_id="mrben-logic-02",
        benchmark="MR-Ben",
        subject="logic",
        question="In a tournament with 8 players, every pair plays exactly one match with no ties. Prove whether it is possible that all 8 players finish with distinct win totals.",
        solution_steps=[
            "Step 0: In a tournament with 8 players, the number of matches each player plays is 7.",
            "Step 1: Therefore, the possible win totals for any individual player are integers in the range [0, 7].",
            "Step 2: There are exactly 8 possible distinct integer win values: {0, 1, 2, 3, 4, 5, 6, 7}.",
            "Step 3: Since there are 8 players and 8 distinct values, one player must have 7 wins (beat everyone) and one player must have 0 wins (lost to everyone).",
            "Step 4: But the player with 7 wins must have beaten the player with 0 wins in their match, which means the 0-win player lost to the 7-win player.",
            "Step 5: This creates a logical contradiction because both conditions cannot hold simultaneously, so distinct win totals are impossible.",
        ],
        ground_truth_correctness="incorrect",
        ground_truth_first_error_step=4,
        ground_truth_error_reason="Step 4 states that the player with 7 wins beating the player with 0 wins is a contradiction, but it is completely consistent and required (the 7-win player won all 7 games, and the 0-win player lost all 7 games). In fact, a round-robin tournament CAN have distinct win scores (0 through N-1 in transitive tournaments).",
        ground_truth_answer="Distinct win totals {0, 1, 2, 3, 4, 5, 6, 7} are mathematically possible in a transitive tournament.",
    ),
    "mrben-logic-03": ReasoningTaskItem(
        task_id="mrben-logic-03",
        benchmark="MR-Ben",
        subject="logic",
        question="A box contains 10 red balls and 10 blue balls. You draw balls at random without replacement. What is the probability that the 10th ball drawn is red?",
        solution_steps=[
            "Step 0: By symmetry and exchangeability of random draws without replacement, each position in the draw sequence is equally likely to be any of the 20 balls.",
            "Step 1: The 10th position in the sequence has the same marginal probability distribution as the 1st position.",
            "Step 2: The probability of drawing a red ball on the 1st draw is 10/20 = 1/2.",
            "Step 3: Therefore, the probability that the 10th ball drawn is red is 1/2.",
        ],
        ground_truth_correctness="correct",
        ground_truth_first_error_step=-1,
        ground_truth_error_reason="No error. By symmetry of permutations without replacement, marginal probability is constant at 1/2.",
        ground_truth_answer="1/2",
    ),
    "mrben-logic-04": ReasoningTaskItem(
        task_id="mrben-logic-04",
        benchmark="MR-Ben",
        subject="logic",
        question="Given premises: (1) All poets are daydreamers. (2) Some daydreamers are not mathematicians. Can we deduce that some poets are not mathematicians?",
        solution_steps=[
            "Step 0: Express Premise 1 in predicate logic: For all x, Poet(x) -> Daydreamer(x).",
            "Step 1: Express Premise 2: Exists x such that Daydreamer(x) and not Mathematician(x).",
            "Step 2: Let 'd' be a daydreamer who is not a mathematician (from Premise 2).",
            "Step 3: From Premise 1, since all poets are daydreamers, 'd' must be a poet.",
            "Step 4: Since 'd' is a poet and not a mathematician, we deduce that some poets are not mathematicians.",
        ],
        ground_truth_correctness="incorrect",
        ground_truth_first_error_step=3,
        ground_truth_error_reason="Step 3 commits the fallacy of affirming the consequent. 'All poets are daydreamers' does not imply that every daydreamer 'd' is a poet.",
        ground_truth_answer="The conclusion cannot be deduced; the argument is invalid.",
    ),
    "mrben-logic-05": ReasoningTaskItem(
        task_id="mrben-logic-05",
        benchmark="MR-Ben",
        subject="logic",
        question="Find the minimum number of balance scale weighings needed to identify 1 counterfeit (heavier) coin among 27 identical-looking coins.",
        solution_steps=[
            "Step 0: Each weighing on a two-pan balance scale yields 3 possible outcomes: Left heavier, Right heavier, or Balanced.",
            "Step 1: With k weighings, the maximum number of distinguishable states is 3^k.",
            "Step 2: We have 27 coins, so we require 3^k >= 27.",
            "Step 3: Since 3^3 = 27, k = 3 weighings are necessary and sufficient.",
            "Step 4: In weighing 1, divide 27 coins into 3 equal groups of 9 coins each (Group A, B, C) and weigh A vs B.",
            "Step 5: The heavier group (or C if balanced) contains the counterfeit coin. Repeat recursively with 9 coins (3 weighings total).",
        ],
        ground_truth_correctness="correct",
        ground_truth_first_error_step=-1,
        ground_truth_error_reason="No error. Ternary search strategy with 3 weighings is optimal and complete.",
        ground_truth_answer="3 weighings",
    ),

    # =========================================================================
    # 📐 OLYMPIAD & MATHEMATICAL PROCESS REASONING (5 Tasks)
    # =========================================================================
    "processbench-olympiad-01": ReasoningTaskItem(
        task_id="processbench-olympiad-01",
        benchmark="ProcessBench",
        subject="olympiad_math",
        question="Solve for all real x: sqrt(x + 3 - 4*sqrt(x - 1)) + sqrt(x + 8 - 6*sqrt(x - 1)) = 1.",
        solution_steps=[
            "Step 0: Let u = sqrt(x - 1), where u >= 0, so x - 1 = u^2 and x = u^2 + 1.",
            "Step 1: Substitute x into first radical: x + 3 - 4*sqrt(x - 1) = u^2 + 1 + 3 - 4u = u^2 - 4u + 4 = (u - 2)^2.",
            "Step 2: Substitute x into second radical: x + 8 - 6*sqrt(x - 1) = u^2 + 1 + 8 - 6u = u^2 - 6u + 9 = (u - 3)^2.",
            "Step 3: The equation simplifies to: (u - 2) + (u - 3) = 1.",
            "Step 4: Solving: 2u - 5 = 1 => 2u = 6 => u = 3.",
            "Step 5: From u = 3: sqrt(x - 1) = 3 => x - 1 = 9 => x = 10.",
        ],
        ground_truth_correctness="incorrect",
        ground_truth_first_error_step=3,
        ground_truth_error_reason="Step 3 drops the absolute value signs: sqrt((u - 2)^2) + sqrt((u - 3)^2) is |u - 2| + |u - 3| = 1, which holds for all u in the entire interval [2, 3], not just a single point u=3.",
        ground_truth_answer="The solution set is the interval 2 <= u <= 3, corresponding to 5 <= x <= 10.",
    ),
    "processbench-olympiad-02": ReasoningTaskItem(
        task_id="processbench-olympiad-02",
        benchmark="ProcessBench",
        subject="olympiad_math",
        question="Find all prime numbers p such that 2^p + p^2 is also a prime number.",
        solution_steps=[
            "Step 0: Test small primes: For p = 2: 2^2 + 2^2 = 4 + 4 = 8 (not prime).",
            "Step 1: For p = 3: 2^3 + 3^2 = 8 + 9 = 17 (which is prime). So p = 3 is a solution.",
            "Step 2: For p > 3, every prime p is coprime to 3, so p = 1 (mod 3) or p = 2 (mod 3), meaning p^2 = 1 (mod 3).",
            "Step 3: Consider 2^p modulo 3. Since p is odd for p > 3, 2^p = (-1)^p = -1 = 2 (mod 3).",
            "Step 4: Therefore, for all primes p > 3: 2^p + p^2 = 2 + 1 = 3 = 0 (mod 3).",
            "Step 5: Since 2^p + p^2 > 3 for p > 3, it is always a composite multiple of 3. Thus p = 3 is the unique solution.",
        ],
        ground_truth_correctness="correct",
        ground_truth_first_error_step=-1,
        ground_truth_error_reason="No error. Complete modulo 3 residue analysis proves p=3 is the unique prime.",
        ground_truth_answer="p = 3",
    ),
    "processbench-olympiad-03": ReasoningTaskItem(
        task_id="processbench-olympiad-03",
        benchmark="ProcessBench",
        subject="olympiad_math",
        question="Evaluate the infinite product: P = prod_{n=2}^{infinity} (1 - 1/n^2).",
        solution_steps=[
            "Step 0: Factor the general term: (1 - 1/n^2) = (n^2 - 1)/n^2 = ((n - 1)(n + 1)) / (n * n).",
            "Step 1: Write the product for N terms: P_N = prod_{n=2}^N ((n - 1)/n) * prod_{n=2}^N ((n + 1)/n).",
            "Step 2: The first product is: (1/2) * (2/3) * (3/4) * ... * ((N - 1)/N) = 1/N.",
            "Step 3: The second product is: (3/2) * (4/3) * (5/4) * ... * ((N + 1)/N) = (N + 1)/2.",
            "Step 4: The partial product is: P_N = (1/N) * ((N + 1)/2) = (N + 1)/(2N).",
            "Step 5: Taking the limit as N -> infinity: lim_{N -> infinity} (N + 1)/(2N) = 1/2.",
        ],
        ground_truth_correctness="correct",
        ground_truth_first_error_step=-1,
        ground_truth_error_reason="No error. Telescoping product derivation is correct and converges to 1/2.",
        ground_truth_answer="1/2",
    ),
    "processbench-olympiad-04": ReasoningTaskItem(
        task_id="processbench-olympiad-04",
        benchmark="ProcessBench",
        subject="olympiad_math",
        question="Find the minimum value of f(x, y) = x^2 + 2y^2 subject to the constraint x + y = 1 for real numbers x and y.",
        solution_steps=[
            "Step 0: Substitute the constraint y = 1 - x into f(x, y).",
            "Step 1: f(x) = x^2 + 2(1 - x)^2 = x^2 + 2(1 - 2x + x^2) = 3x^2 - 4x + 2.",
            "Step 2: Differentiate with respect to x: f'(x) = 6x - 4.",
            "Step 3: Set f'(x) = 0 => 6x = 4 => x = 2/3.",
            "Step 4: Since f''(x) = 6 > 0, x = 2/3 gives a local minimum.",
            "Step 5: Substitute x = 2/3 into f: f(2/3) = 3(4/9) - 4(2/3) + 2 = 4/3 - 8/3 + 2 = -4/3 + 6/3 = 2/3.",
        ],
        ground_truth_correctness="correct",
        ground_truth_first_error_step=-1,
        ground_truth_error_reason="No error. Calculus minimization is correct.",
        ground_truth_answer="2/3",
    ),
    "processbench-olympiad-05": ReasoningTaskItem(
        task_id="processbench-olympiad-05",
        benchmark="ProcessBench",
        subject="olympiad_math",
        question="Determine the number of integer solutions to x^2 - y^2 = 2026.",
        solution_steps=[
            "Step 0: Factor the left side as difference of squares: (x - y)(x + y) = 2026.",
            "Step 1: Notice that (x - y) + (x + y) = 2x, which is always even.",
            "Step 2: Therefore, (x - y) and (x + y) must have the same parity (both even or both odd).",
            "Step 3: If both factors are even, their product (x - y)(x + y) must be divisible by 4.",
            "Step 4: If both factors are odd, their product must be odd.",
            "Step 5: Factor 2026: 2026 = 2 * 1013, which is even but NOT divisible by 4 (2026 = 2 mod 4).",
            "Step 6: Therefore, there are no integer solutions.",
        ],
        ground_truth_correctness="correct",
        ground_truth_first_error_step=-1,
        ground_truth_error_reason="No error. Parity and mod 4 constraint proof is sound and complete.",
        ground_truth_answer="0 solutions",
    ),
}


def get_reasoning_task(task_id: str) -> ReasoningTaskItem:
    """Retrieves a reasoning task by ID."""
    if task_id not in HARD_REASONING_TASKS:
        raise KeyError(f"Reasoning task ID '{task_id}' not found.")
    return HARD_REASONING_TASKS[task_id]


def list_reasoning_tasks() -> List[ReasoningTaskItem]:
    """Returns all 15 hard reasoning benchmark tasks."""
    return list(HARD_REASONING_TASKS.values())
