from __future__ import annotations
import astor
import graphviz as gv
import os
import ast
import sys
import logging
from typing import Dict, List, Tuple, Set, Optional, Type, Any

logging.basicConfig(level=logging.DEBUG)


class BlockId:
    counter: int = 0

    @classmethod
    def gen_block_id(cls) -> int:
        cls.counter += 1
        return cls.counter


class BasicBlock(object):
    def __init__(self, bid: int):
        self.bid: int = bid
        self.stmts: List[ast.AST] = []
        self.calls: List[str] = []
        self.prev: List[int] = []
        self.next: List[int] = []

    def is_empty(self) -> bool:
        return len(self.stmts) == 0

    def has_next(self) -> bool:
        return len(self.next) != 0

    def has_previous(self) -> bool:
        return len(self.prev) != 0

    def remove_from_prev(self, prev_bid: int) -> None:
        if prev_bid in self.prev:
            self.prev.remove(prev_bid)

    def remove_from_next(self, next_bid: int) -> None:
        if next_bid in self.next:
            self.next.remove(next_bid)

    def stmts_to_code(self) -> str:
        code = str(self.bid) + "\n"
        if self.stmts and type(self.stmts[0]) == ast.Module:
            code += "Module"
            return code
        for stmt in self.stmts:
            line = astor.to_source(stmt)
            code += (
                line.split("\n")[0] + "\n"
                if type(stmt)
                   in [ast.If, ast.For, ast.While, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
                else line
            )
        return code

    def calls_to_code(self) -> str:
        return "\n".join(self.calls)


class FuncBlock(BasicBlock):
    def __init__(self, bid: int):
        super().__init__(bid)
        self.name: Optional[str] = None
        self.paras: List[str] = []


class CallBlock(BasicBlock):
    def __init__(self, bid: int):
        super().__init__(bid)
        self.args: List[str] = []
        self.call = bid
        self.exit = BlockId.gen_block_id()


class TryBlock(BasicBlock):
    def __init__(self, bid: int):
        super().__init__(bid)


class CFG:
    def __init__(self, name: str):
        self.name: str = name

        # I am sure that in original code variable asynchr is not used
        # And I think list finalblocks is also not used.

        self.start: Optional[BasicBlock] = None
        # Function name to (Args, CFG)
        self.func_calls: Dict[str, (List[str, ast.AST], CFG)] = {}
        self.classes: Dict[str, CFG] = {}
        self.blocks: Dict[int, BasicBlock] = {}
        self.edges: Dict[Tuple[int, int], Type[ast.AST]] = {}
        self.graph: Optional[gv.dot.Digraph] = None

        self.flows = set()

    def _traverse(
            self, block: BasicBlock, visited: Set[int] = set(), calls: bool = True
    ) -> None:
        if block.bid not in visited:
            visited.add(block.bid)
            self.graph.node(str(block.bid), label=block.stmts_to_code())
            if calls and block.calls:
                self.graph.node(
                    str(block.bid) + "_call",
                    label=block.calls_to_code(),
                    _attributes={"shape": "box"},
                )
                self.graph.edge(
                    str(block.bid),
                    str(block.bid) + "_call",
                    label="calls",
                    _attributes={"style": "dashed"},
                )

            for next_bid in block.next:
                self._traverse(self.blocks[next_bid], visited, calls=calls)
                self.graph.edge(
                    str(block.bid),
                    str(next_bid),
                    label=astor.to_source(self.edges[(block.bid, next_bid)])
                    if self.edges[(block.bid, next_bid)]
                    else "",
                )

    def _show(self, fmt: str = "png", calls: bool = True) -> gv.dot.Digraph:
        # self.graph = gv.Digraph(name='cluster_'+self.name, format=fmt, graph_attr={'label': self.name})
        self.graph = gv.Digraph(name="cluster_" + self.name, format=fmt)
        self._traverse(self.start, calls=calls)
        for k, v in self.func_calls.items():
            self.graph.subgraph(v[1]._show(fmt, calls))
        for class_name, classCFG in self.classes.items():
            self.graph.subgraph(classCFG._show(fmt, calls))
        return self.graph

    def show(
            self,
            filepath: str = "./output",
            fmt: str = "png",
            calls: bool = True,
            show: bool = True,
    ) -> None:
        self._show(fmt, calls)
        path = os.path.normpath(filepath)
        self.graph.render(path, view=show, cleanup=True)


class CFGVisitor(ast.NodeVisitor):
    invertComparators: Dict[Type[ast.AST], Type[ast.AST]] = {
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
        ast.Lt: ast.GtE,
        ast.LtE: ast.Gt,
        ast.Gt: ast.LtE,
        ast.GtE: ast.Lt,
        ast.Is: ast.IsNot,
        ast.IsNot: ast.Is,
        ast.In: ast.NotIn,
        ast.NotIn: ast.In,
    }

    def __init__(self):
        super().__init__()
        self.loop_stack: List[BasicBlock] = []
        self.ifExp = False
        self.cfg: Optional[CFG] = None
        self.curr_block: Optional[BasicBlock] = None

        self.loop_guard_stack: List[BasicBlock] = []

    def build(self, name: str, tree: ast.Module) -> CFG:
        self.cfg = CFG(name)
        self.curr_block = self.new_block()
        self.cfg.start = self.curr_block

        self.visit(tree)
        self.remove_empty_blocks(self.cfg.start)
        return self.cfg

    def replace_block(self, new_block: BasicBlock):
        old_bid: int = self.curr_block.bid
        for prev_bid in self.curr_block.prev:
            block: Type[BasicBlock] = self.cfg.blocks[prev_bid]
            block.remove_from_next(old_bid)
            self.add_edge(prev_bid, new_block.bid)
        for next_bid in self.curr_block.next:
            block: Type[BasicBlock] = self.cfg.blocks[next_bid]
            block.remove_from_prev(old_bid)
            self.add_edge(new_block.bid, next_bid)
        self.curr_block = new_block

    def new_block(self) -> BasicBlock:
        bid: int = BlockId.gen_block_id()
        self.cfg.blocks[bid] = BasicBlock(bid)
        return self.cfg.blocks[bid]

    def new_func_block(self, bid: int) -> FuncBlock:
        self.cfg.blocks[bid] = FuncBlock(bid)
        return self.cfg.blocks[bid]

    def new_call_block(self, bid: int) -> CallBlock:
        self.cfg.blocks[bid] = CallBlock(bid)
        return self.cfg.blocks[bid]

    def add_stmt(self, block: BasicBlock, stmt: ast.AST) -> None:
        block.stmts.append(stmt)

    def add_edge(self, frm_id: int, to_id: int, condition=None) -> BasicBlock:
        self.cfg.blocks[frm_id].next.append(to_id)
        self.cfg.blocks[to_id].prev.append(frm_id)
        self.cfg.edges[(frm_id, to_id)] = condition

        self.cfg.flows.add((frm_id, to_id))
        return self.cfg.blocks[to_id]

    def add_loop_block(self) -> BasicBlock:
        if self.curr_block.is_empty() and not self.curr_block.has_next():
            return self.curr_block
        else:
            loop_block = self.new_block()
            self.add_edge(self.curr_block.bid, loop_block.bid)
            return loop_block

    def add_subgraph(self, tree: ast.FunctionDef) -> None:
        arg_list: List[(str, Optional[ast.AST])] = []

        tmp_arg_list: List[str] = []
        for arg in tree.args.args:
            tmp_arg_list.append(arg.arg)
        len_arg_list = len(tmp_arg_list)
        len_defaults = len(tree.args.defaults)
        diff = len_arg_list - len_defaults
        index = 0
        while diff > 0:
            arg_list.append((tmp_arg_list[index], None))
            diff -= 1
            index += 1

        for default in tree.args.defaults:
            arg_list.append((tmp_arg_list[index], default))
            index += 1

        self.cfg.func_calls[tree.name] = (
            arg_list,
            CFGVisitor().build(tree.name, ast.Module(body=tree.body)),
        )

    def add_ClassCFG(self, node: ast.ClassDef):
        class_body: ast.Module = ast.Module(body=node.body)
        class_cfg: CFG = CFGVisitor().build(node.name, class_body)
        self.cfg.classes[node.name] = class_cfg

    def add_condition(
            self, cond1: Optional[Type[ast.AST]], cond2: Optional[Type[ast.AST]]
    ) -> Optional[ast.AST]:
        if cond1 and cond2:
            return ast.BoolOp(ast.And(), values=[cond1, cond2])
        else:
            return cond1 if cond1 else cond2

    # not tested
    def remove_empty_blocks(self, block: BasicBlock, visited: Set[int] = set()) -> None:
        if block.bid not in visited:
            visited.add(block.bid)
            if block.is_empty():
                for prev_bid in block.prev:
                    prev_block = self.cfg.blocks[prev_bid]
                    for next_bid in block.next:
                        next_block = self.cfg.blocks[next_bid]
                        self.add_edge(
                            prev_bid,
                            next_bid,
                            self.add_condition(
                                self.cfg.edges.get((prev_bid, block.bid)),
                                self.cfg.edges.get((block.bid, next_bid)),
                            ),
                        )
                        self.cfg.edges.pop((block.bid, next_bid), None)
                        next_block.remove_from_prev(block.bid)
                        self.cfg.flows.remove((block.bid, next_block.bid))
                    self.cfg.edges.pop((prev_bid, block.bid), None)
                    prev_block.remove_from_next(block.bid)
                    self.cfg.flows.remove((prev_block.bid, block.bid))
                block.prev.clear()
                for next_bid in block.next:
                    self.remove_empty_blocks(self.cfg.blocks[next_bid], visited)
                block.next.clear()

            else:
                for next_bid in block.next:
                    self.remove_empty_blocks(self.cfg.blocks[next_bid], visited)

    def invert(self, node: Type[ast.AST]) -> ast.AST:
        if type(node) == ast.Compare:
            if len(node.ops) == 1:
                return ast.Compare(
                    left=node.left,
                    ops=[self.invertComparators[type(node.ops[0])]()],
                    comparators=node.comparators,
                )
            else:
                tmpNode = ast.BoolOp(
                    op=ast.And(),
                    values=[
                        ast.Compare(
                            left=node.left,
                            ops=[node.ops[0]],
                            comparators=[node.comparators[0]],
                        )
                    ],
                )
                for i in range(0, len(node.ops) - 1):
                    tmpNode.values.append(
                        ast.Compare(
                            left=node.comparators[i],
                            ops=[node.ops[i + 1]],
                            comparators=[node.comparators[i + 1]],
                        )
                    )
                return self.invert(tmpNode)
        elif isinstance(node, ast.BinOp) and type(node.op) in self.invertComparators:
            return ast.BinOp(
                node.left, self.invertComparators[type(node.op)](), node.right
            )
        elif type(node) == ast.NameConstant and type(node.value) == bool:
            return ast.NameConstant(value=not node.value)
        elif type(node) == ast.BoolOp:
            return ast.BoolOp(
                values=[self.invert(x) for x in node.values],
                op={ast.And: ast.Or(), ast.Or: ast.And()}.get(type(node.op)),
            )
        elif type(node) == ast.UnaryOp:
            return self.UnaryopInvert(node)
        else:
            return ast.UnaryOp(op=ast.Not(), operand=node)

    def UnaryopInvert(self, node: Type[ast.AST]) -> Type[ast.AST]:
        if type(node.op) == ast.UAdd:
            return ast.UnaryOp(op=ast.USub(), operand=node.operand)
        elif type(node.op) == ast.USub:
            return ast.UnaryOp(op=ast.UAdd(), operand=node.operand)
        elif type(node.op) == ast.Invert:
            return ast.UnaryOp(op=ast.Not(), operand=node)
        else:
            return node.operand

    # def boolinvert(self, node:Type[ast.AST]) -> Type[ast.AST]:
    #     value = []
    #     for item in node.values:
    #         value.append(self.invert(item))
    #     if type(node.op) == ast.Or:
    #         return ast.BoolOp(values = value, op = ast.And())
    #     elif type(node.op) == ast.And:
    #         return ast.BoolOp(values = value, op = ast.Or())

    def combine_conditions(self, node_list: List[Type[ast.AST]]) -> Type[ast.AST]:
        return (
            node_list[0]
            if len(node_list) == 1
            else ast.BoolOp(op=ast.And(), values=node_list)
        )

    def generic_visit(self, node):
        if type(node) in [ast.AsyncFunctionDef]:
            self.add_stmt(self.curr_block, node)
            self.add_subgraph(node)
            return
        if type(node) in [ast.AnnAssign]:
            self.add_stmt(self.curr_block, node)
        super().generic_visit(node)

    def get_function_name(self, node: Type[ast.AST]) -> str:
        if type(node) == ast.Name:
            return node.id
        elif type(node) == ast.Attribute:
            return self.get_function_name(node.value) + "." + node.attr
        elif type(node) == ast.Str:
            return node.s
        elif type(node) == ast.Subscript:
            return node.value.id
        elif type(node) == ast.Lambda:
            return "lambda function"

    def populate_body(self, body_list: List[Type[ast.AST]], to_bid: int) -> None:
        for child in body_list:
            self.visit(child)
        if not self.curr_block.next:
            self.add_edge(self.curr_block.bid, to_bid)

    def visit_Module(self, node: ast.Module) -> None:
        self.add_stmt(self.curr_block, node)
        new_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, new_block.bid)
        self.curr_block = new_block

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.add_stmt(self.curr_block, node)
        new_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, new_block.bid)
        self.curr_block = new_block

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.add_stmt(self.curr_block, node)
        new_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, new_block.bid)
        self.curr_block = new_block

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        new_func_block: FuncBlock = self.new_func_block(self.curr_block.bid)
        self.curr_block = new_func_block
        self.add_stmt(self.curr_block, node)
        self.add_subgraph(node)

        next_block = self.new_block()
        self.add_edge(self.curr_block.bid, next_block.bid)
        self.curr_block = next_block

    def visit_ClassDef(self, node: ast.ClassDef):
        self.add_stmt(self.curr_block, node)
        self.add_ClassCFG(node)

        next_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, next_block.bid)
        self.curr_block = next_block

    # TODO: change all those registers to stacks!
    def visit_Assign(self, node: ast.Assign):
        self.generic_visit(node)

        if (
                type(node.value)
                in [ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Lambda]
                and len(node.targets) == 1
                and type(node.targets[0]) == ast.Name
        ):  # is this entire statement necessary?
            if type(node.value) == ast.ListComp:
                self.add_stmt(
                    self.curr_block,
                    ast.Assign(
                        targets=[ast.Name(id=node.targets[0].id, ctx=ast.Store())],
                        value=ast.List(elts=[], ctx=ast.Load()),
                    ),
                )
                self.listCompReg = (node.targets[0].id, node.value)
            elif type(node.value) == ast.SetComp:
                self.add_stmt(
                    self.curr_block,
                    ast.Assign(
                        targets=[ast.Name(id=node.targets[0].id, ctx=ast.Store())],
                        value=ast.Call(
                            func=ast.Name(id="set", ctx=ast.Load()),
                            args=[],
                            keywords=[],
                        ),
                    ),
                )
                self.setCompReg = (node.targets[0].id, node.value)
            elif type(node.value) == ast.DictComp:
                self.add_stmt(
                    self.curr_block,
                    ast.Assign(
                        targets=[ast.Name(id=node.targets[0].id, ctx=ast.Store())],
                        value=ast.Dict(keys=[], values=[]),
                    ),
                )
                self.dictCompReg = (node.targets[0].id, node.value)
            elif type(node.value) == ast.GeneratorExp:
                self.add_stmt(
                    self.curr_block,
                    ast.Assign(
                        targets=[ast.Name(id=node.targets[0].id, ctx=ast.Store())],
                        value=ast.Call(
                            func=ast.Name(
                                id="__" + node.targets[0].id + "Generator__",
                                ctx=ast.Load(),
                            ),
                            args=[],
                            keywords=[],
                        ),
                    ),
                )
                self.genExpReg = (node.targets[0].id, node.value)
            else:
                self.lambdaReg = (node.targets[0].id, node.value)
        else:
            self.add_stmt(self.curr_block, node)
            next_block = self.new_block()
            self.add_edge(self.curr_block.bid, next_block.bid)
            self.curr_block = next_block

    def visit_Call(self, node: ast.Call) -> None:
        if type(node.func) == ast.Lambda:
            self.lambdaReg = ("Anonymous Function", node.func)
            self.generic_visit(node)
        else:
            call_block: CallBlock = self.new_call_block(self.curr_block.bid)
            self.curr_block = call_block
            self.add_stmt(self.curr_block, node)

            new_block = self.new_block()
            self.add_edge(self.curr_block.bid, new_block.bid)
            self.curr_block = new_block
            # self.curr_block.calls.append(self.get_function_name(node.func))

    def visit_AugAssign(self, node: ast.AugAssign) -> Any:
        self.add_stmt(self.curr_block, node)
        new_block = self.new_block()
        self.add_edge(self.curr_block.bid, new_block.bid)
        self.curr_block = new_block

    def visit_If(self, node: ast.If) -> None:
        # Add the If statement at the end of the current block.
        self.add_stmt(self.curr_block, node)

        # Create a block for the code after the if-else.
        after_if_block: BasicBlock = self.new_block()

        # Create a new block for the body of the if.
        if_body_block: BasicBlock = self.add_edge(self.curr_block.bid, self.new_block().bid)

        # New block for the body of the else if there is an else clause.
        if node.orelse:
            self.curr_block = self.add_edge(
                self.curr_block.bid, self.new_block().bid
            )

            # Visit the children in the body of the else to populate the block.
            self.populate_body(node.orelse, after_if_block.bid)
        else:
            self.add_edge(
                self.curr_block.bid, after_if_block.bid
            )
        # Visit children to populate the if block.
        self.curr_block: BasicBlock = if_body_block
        self.populate_body(node.body, after_if_block.bid)

        # Continue building the CFG in the after-if block.
        self.curr_block: BasicBlock = after_if_block

    def visit_For(self, node: ast.While) -> None:
        loop_guard = self.add_loop_block()
        self.curr_block = loop_guard
        self.add_stmt(self.curr_block, node)
        self.loop_guard_stack.append(loop_guard)

        # New block for the body of the for-loop.
        for_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, for_block.bid)
        after_for_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, after_for_block.bid)
        self.loop_stack.append(after_for_block)
        if not node.orelse:
            # Block of code after the for loop.
            self.add_edge(self.curr_block.bid, after_for_block.bid)

            # self.loop_stack.append(after_for_block)
            self.curr_block = for_block
            self.populate_body(node.body, loop_guard.bid)
        else:
            # Block of code after the for loop.
            or_else_block: BasicBlock = self.new_block()
            orelse_block = self.add_edge(
                self.curr_block.bid,
                or_else_block.bid
            )

            # self.loop_stack.append(after_for_block)
            self.curr_block = for_block
            self.populate_body(node.body, loop_guard.bid)

            self.curr_block = orelse_block
            self.populate_body(node.orelse, after_for_block.bid)

        # Continue building the CFG in the after-for block.
        self.curr_block = after_for_block
        self.loop_stack.pop()
        self.loop_guard_stack.pop()

    # ignore the case when using set or dict comprehension or generator expression but the result is not assigned to a variable
    def visit_Expr(self, node: ast.Expr):
        if type(node.value) == ast.ListComp and type(node.value.elt) == ast.Call:
            self.listCompReg = (None, node.value)
        elif type(node.value) == ast.Lambda:
            self.lambdaReg = ("Anonymous Function", node.value)
        # elif type(node.value) == ast.Call and type(node.value.func) == ast.Lambda:
        #     self.lambdaReg = ('Anonymous Function', node.value.func)
        elif type(node.value == ast.Call):
            pass
        else:
            self.add_stmt(self.curr_block, node)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        loop_guard: BasicBlock = self.add_loop_block()
        self.curr_block = loop_guard
        self.add_stmt(loop_guard, node)
        self.loop_guard_stack.append(loop_guard)

        # New block for the case where the test in the while is False.
        after_while_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, after_while_block.bid)
        self.loop_stack.append(after_while_block)

        if not node.orelse:
            # New block for the case where the test in the while is True.
            # Populate the while block.
            while_body_block: BasicBlock = self.new_block()
            self.add_edge(self.curr_block.bid, while_body_block.bid)
            self.curr_block = while_body_block

            self.populate_body(node.body, loop_guard.bid)
        else:
            or_else_block: BasicBlock = self.new_block()
            self.add_edge(self.curr_block.bid, or_else_block.bid)
            while_body_block: BasicBlock = self.new_block()
            self.add_edge(self.curr_block.bid, while_body_block.bid)
            self.curr_block = while_body_block
            self.populate_body(node.body, loop_guard.bid)

            self.curr_block = or_else_block
            self.populate_body(node.orelse, after_while_block.bid)

        # Continue building the CFG in the after-while block.
        self.curr_block = after_while_block
        self.loop_stack.pop()
        self.loop_guard_stack.pop()

    def visit_Break(self, node: ast.Break) -> None:
        assert len(self.loop_stack), "Found break not inside loop"
        self.add_stmt(self.curr_block, node)
        # self.add_edge(self.curr_block.bid, self.loop_stack[-1].bid, ast.Break())
        self.add_edge(self.curr_block.bid, self.loop_stack[-1].bid)

    # ToDO: final blocks to be add
    def visit_Return(self, node: ast.Return) -> None:
        if type(node.value) == ast.IfExp:
            self.ifExp = True
            self.generic_visit(node)
            self.ifExp = False
        else:
            self.add_stmt(self.curr_block, node)
        # self.cfg.finalblocks.append(self.curr_block)
        # Continue in a new block but without any jump to it -> all code after
        # the return statement will not be included in the CFG.
        self.curr_block = self.new_block()

    def visit_Pass(self, node: ast.Pass) -> None:
        self.add_stmt(self.curr_block, node)
        new_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, new_block.bid)
        self.curr_block = new_block

    def visit_Continue(self, node: ast.Continue):
        self.add_stmt(self.curr_block, node)
        assert self.loop_guard_stack
        self.add_edge(self.curr_block.bid, self.loop_guard_stack[-1].bid)

    # assert type check
    def visit_Assert(self, node):
        self.add_stmt(self.curr_block, node)
        # If the assertion fails, the current flow ends, so the fail block is a
        # final block of the CFG.
        # self.cfg.finalblocks.append(self.add_edge(self.curr_block.bid, self.new_block().bid, self.invert(node.test)))
        # If the assertion is True, continue the flow of the program.
        # success block
        self.curr_block = self.add_edge(
            self.curr_block.bid, self.new_block().bid, node.test
        )
        self.generic_visit(node)

    def visit_Await(self, node):
        afterawait_block = self.new_block()
        self.add_edge(self.curr_block.bid, afterawait_block.bid)
        self.generic_visit(node)
        self.curr_block = afterawait_block

    def visit_DictComp_Rec(
            self, generators: List[Type[ast.AST]]
    ) -> List[Type[ast.AST]]:
        if not generators:
            if self.dictCompReg[0]:  # bug if there is else statement in comprehension
                return [
                    ast.Assign(
                        targets=[
                            ast.Subscript(
                                value=ast.Name(id=self.dictCompReg[0], ctx=ast.Load()),
                                slice=ast.Index(value=self.dictCompReg[1].key),
                                ctx=ast.Store(),
                            )
                        ],
                        value=self.dictCompReg[1].value,
                    )
                ]
            # else: # not supported yet
            #     return [ast.Expr(value=self.dictCompReg[1].elt)]
        else:
            return [
                ast.For(
                    target=generators[-1].target,
                    iter=generators[-1].iter,
                    body=[
                        ast.If(
                            test=self.combine_conditions(generators[-1].ifs),
                            body=self.visit_DictComp_Rec(generators[:-1]),
                            orelse=[],
                        )
                    ]
                    if generators[-1].ifs
                    else self.visit_DictComp_Rec(generators[:-1]),
                    orelse=[],
                )
            ]

    def visit_DictComp(self, node):
        try:  # try may change to checking if self.dictCompReg exists
            self.generic_visit(
                ast.Module(self.visit_DictComp_Rec(self.dictCompReg[1].generators))
            )
        except:
            pass
        finally:
            self.dictCompReg = None

    def visit_GeneratorExp_Rec(
            self, generators: List[Type[ast.AST]]
    ) -> List[Type[ast.AST]]:
        if not generators:
            self.generic_visit(
                self.genExpReg[1].elt
            )  # the location of the node may be wrong
            if self.genExpReg[0]:  # bug if there is else statement in comprehension
                return [ast.Expr(value=ast.Yield(value=self.genExpReg[1].elt))]
        else:
            return [
                ast.For(
                    target=generators[-1].target,
                    iter=generators[-1].iter,
                    body=[
                        ast.If(
                            test=self.combine_conditions(generators[-1].ifs),
                            body=self.visit_GeneratorExp_Rec(generators[:-1]),
                            orelse=[],
                        )
                    ]
                    if generators[-1].ifs
                    else self.visit_GeneratorExp_Rec(generators[:-1]),
                    orelse=[],
                )
            ]

    def visit_GeneratorExp(self, node):
        try:  # try may change to checking if self.genExpReg exists
            self.generic_visit(
                ast.FunctionDef(
                    name="__" + self.genExpReg[0] + "Generator__",
                    args=ast.arguments(
                        args=[],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=self.visit_GeneratorExp_Rec(self.genExpReg[1].generators),
                    decorator_list=[],
                    returns=None,
                )
            )
        except:
            pass
        finally:
            self.genExpReg = None

    def visit_IfExp_Rec(self, node: ast.AST) -> List[ast.AST]:
        return [
            ast.If(
                test=node.test,
                body=[ast.Return(value=node.body)],
                orelse=self.visit_IfExp_Rec(node.orelse)
                if type(node.orelse) == ast.IfExp
                else [ast.Return(value=node.orelse)],
            )
        ]

    def visit_IfExp(self, node):
        if self.ifExp:
            self.generic_visit(ast.Module(self.visit_IfExp_Rec(node)))

    def visit_Lambda(self, node):  # deprecated since there is autopep8
        self.add_subgraph(
            ast.FunctionDef(
                name=self.lambdaReg[0],
                args=node.args,
                body=[ast.Return(value=node.body)],
                decorator_list=[],
                returns=None,
            )
        )
        self.lambdaReg = None

    def visit_ListComp_Rec(
            self, generators: List[Type[ast.AST]]
    ) -> List[ast.AST]:
        if not generators:
            self.generic_visit(
                self.listCompReg[1].elt
            )  # the location of the node may be wrong
            if self.listCompReg[0]:  # bug if there is else statement in comprehension
                return [
                    ast.Expr(
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id=self.listCompReg[0], ctx=ast.Load()),
                                attr="append",
                                ctx=ast.Load(),
                            ),
                            args=[self.listCompReg[1].elt],
                            keywords=[],
                        )
                    )
                ]
            else:
                return [ast.Expr(value=self.listCompReg[1].elt)]
        else:
            return [
                ast.For(
                    target=generators[-1].target,
                    iter=generators[-1].iter,
                    body=[
                        ast.If(
                            test=self.combine_conditions(generators[-1].ifs),
                            body=self.visit_ListComp_Rec(generators[:-1]),
                            orelse=[],
                        )
                    ]
                    if generators[-1].ifs
                    else self.visit_ListComp_Rec(generators[:-1]),
                    orelse=[],
                )
            ]

    def visit_ListComp(self, node: ast.ListComp):
        try:  # try may change to checking if self.listCompReg exists
            self.generic_visit(
                ast.Module(self.visit_ListComp_Rec(self.listCompReg[1].generators))
            )
        except:
            pass
        finally:
            self.listCompReg = None

    def visit_Raise(self, node):
        self.add_stmt(self.curr_block, node)
        self.curr_block = self.new_block()

    def visit_SetComp_Rec(self, generators: List[Type[ast.AST]]) -> List[Type[ast.AST]]:
        if not generators:
            self.generic_visit(
                self.setCompReg[1].elt
            )  # the location of the node may be wrong
            if self.setCompReg[0]:
                return [
                    ast.Expr(
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id=self.setCompReg[0], ctx=ast.Load()),
                                attr="add",
                                ctx=ast.Load(),
                            ),
                            args=[self.setCompReg[1].elt],
                            keywords=[],
                        )
                    )
                ]
            else:  # not supported yet
                return [ast.Expr(value=self.setCompReg[1].elt)]
        else:
            return [
                ast.For(
                    target=generators[-1].target,
                    iter=generators[-1].iter,
                    body=[
                        ast.If(
                            test=self.combine_conditions(generators[-1].ifs),
                            body=self.visit_SetComp_Rec(generators[:-1]),
                            orelse=[],
                        )
                    ]
                    if generators[-1].ifs
                    else self.visit_SetComp_Rec(generators[:-1]),
                    orelse=[],
                )
            ]

    def visit_SetComp(self, node):
        try:  # try may change to checking if self.setCompReg exists
            self.generic_visit(
                ast.Module(self.visit_SetComp_Rec(self.setCompReg[1].generators))
            )
        except:
            pass
        finally:
            self.setCompReg = None

    def visit_Try(self, node: ast.Try):
        loop_guard = self.add_loop_block()
        self.curr_block = loop_guard
        self.add_stmt(
            loop_guard, ast.Try(body=[], handlers=[], orelse=[], finalbody=[])
        )

        after_try_block = self.new_block()
        self.add_stmt(after_try_block, ast.Name(id="handle errors", ctx=ast.Load()))
        self.populate_body(node.body, after_try_block.bid)

        self.curr_block = after_try_block

        if node.handlers:
            for handler in node.handlers:
                before_handler_block = self.new_block()
                self.curr_block = before_handler_block
                self.add_edge(
                    after_try_block.bid,
                    before_handler_block.bid,
                    handler.type
                    if handler.type
                    else ast.Name(id="Error", ctx=ast.Load()),
                )

                after_handler_block = self.new_block()
                self.add_stmt(
                    after_handler_block, ast.Name(id="end except", ctx=ast.Load())
                )
                self.populate_body(handler.body, after_handler_block.bid)
                self.add_edge(after_handler_block.bid, after_try_block.bid)

        if node.orelse:
            before_else_block = self.new_block()
            self.curr_block = before_else_block
            self.add_edge(
                after_try_block.bid,
                before_else_block.bid,
                ast.Name(id="No Error", ctx=ast.Load()),
            )

            after_else_block = self.new_block()
            self.add_stmt(after_else_block, ast.Name(id="end no error", ctx=ast.Load()))
            self.populate_body(node.orelse, after_else_block.bid)
            self.add_edge(after_else_block.bid, after_try_block.bid)

        finally_block = self.new_block()
        self.curr_block = finally_block

        if node.finalbody:
            self.add_edge(
                after_try_block.bid,
                finally_block.bid,
                ast.Name(id="Finally", ctx=ast.Load()),
            )
            after_finally_block = self.new_block()
            self.populate_body(node.finalbody, after_finally_block.bid)
            self.curr_block = after_finally_block
        else:
            self.add_edge(after_try_block.bid, finally_block.bid)

    def visit_Yield(self, node):
        self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)


if __name__ == "__main__":
    filename = sys.argv[1]
    file = open(filename, "r")
    source = file.read()
    cfg = CFGVisitor().build(filename, ast.parse(source))
    print(cfg.flows)
    cfg.show()
