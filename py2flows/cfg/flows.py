from __future__ import annotations
from py2flows.cfg import randoms
import astor
import graphviz as gv
import os
import ast
import logging
from typing import Dict, List, Tuple, Set, Optional, Type

logging.basicConfig(level=logging.DEBUG)


class BlockId:
    counter: int = 0

    @classmethod
    def gen_block_id(cls) -> int:
        cls.counter += 1
        return cls.counter


class BasicFlow:
    def __init__(self, first_label: int, second_label: int):
        self.first_label: int = first_label
        self.second_label: int = second_label


class FuncFlow:
    def __init__(
            self, call_label: int, entry_label: int, return_label: int, exit_label: int
    ):
        self.call_label: int = call_label
        self.entry_label: int = entry_label
        self.return_label: int = return_label
        self.exit_label: int = exit_label


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
                   in [
                       ast.If,
                       ast.For,
                       ast.While,
                       ast.FunctionDef,
                       ast.AsyncFunctionDef,
                       ast.ClassDef,
                   ]
                else line
            )
        return code

    def calls_to_code(self) -> str:
        return "\n".join(self.calls)

    def __str__(self):
        return "Block ID: {}".format(self.bid)


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

    def __str__(self):
        return "Block ID: {}".format(self.bid)


class TryBlock(BasicBlock):
    def __init__(self, bid: int):
        super().__init__(bid)


class CFG:
    def __init__(self, name: str):
        self.name: str = name

        self.start: Optional[BasicBlock] = None
        self.final_blocks: List[BasicBlock] = []
        # Function name to (Args, CFG)
        self.func_cfgs: Dict[str, (List[str, ast.AST], CFG)] = {}
        self.class_cfgs: Dict[str, CFG] = {}
        self.blocks: Dict[int, BasicBlock] = {}
        self.edges: Dict[Tuple[int, int], Optional[ast.AST]] = {}
        self.graph: Optional[gv.dot.Digraph] = None

        self.flows: Set[Tuple[int, int]] = set()

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

    def _show(self, fmt: str = "pdf", calls: bool = True) -> gv.dot.Digraph:
        # self.graph = gv.Digraph(name='cluster_'+self.name, format=fmt, graph_attr={'label': self.name})
        self.graph = gv.Digraph(name="cluster_" + self.name, format=fmt)
        self._traverse(self.start, calls=calls)
        for k, v in self.func_cfgs.items():
            self.graph.subgraph(v[1]._show(fmt, calls))
        for class_name, classCFG in self.class_cfgs.items():
            self.graph.subgraph(classCFG._show(fmt, calls))
        return self.graph

    def show(
            self,
            filepath: str = "../output",
            fmt: str = "pdf",
            calls: bool = True,
            show: bool = True,
    ) -> None:
        self._show(fmt, calls)
        path = os.path.normpath(filepath)
        self.graph.render(path, view=show, cleanup=True)


class CFGVisitor(ast.NodeVisitor):
    # invertComparators: Dict[Type[ast.AST], Type[ast.AST]] = {
    #     ast.Eq: ast.NotEq,
    #     ast.NotEq: ast.Eq,
    #     ast.Lt: ast.GtE,
    #     ast.LtE: ast.Gt,
    #     ast.Gt: ast.LtE,
    #     ast.GtE: ast.Lt,
    #     ast.Is: ast.IsNot,
    #     ast.IsNot: ast.Is,
    #     ast.In: ast.NotIn,
    #     ast.NotIn: ast.In,
    # }

    def __init__(self):
        super().__init__()
        self.loop_stack: List[BasicBlock] = []
        self.ifExp = False
        self.cfg: Optional[CFG] = None
        self.curr_block: Optional[BasicBlock] = None

        self.loop_guard_stack: List[BasicBlock] = []
        self.list_comp_stack: List[str] = []
        self.set_comp_stack: List[str] = []
        self.dict_comp_stack: List[str] = []
        self.gen_exp_stack = []
        self.lambda_exp_stack = []

    def build(self, name: str, tree: ast.Module) -> CFG:
        self.cfg = CFG(name)
        self.curr_block = self.new_block()
        self.cfg.start = self.curr_block

        self.visit(tree)
        # self.remove_empty_blocks(self.cfg.start)
        self.remove_empty_blocks(self.cfg.start)
        return self.cfg

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

    def add_stmt(self, block: BasicBlock, stmt) -> None:
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

    def add_FuncCFG(self, tree: ast.FunctionDef) -> None:
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

        # tree.body.append(ast.Module(body=[]))
        print(astor.to_source(tree))
        func_cfg: CFG = CFGVisitor().build(tree.name, ast.Module(body=tree.body))
        self.cfg.func_cfgs[tree.name] = (arg_list, func_cfg)

    def add_ClassCFG(self, node: ast.ClassDef):
        class_body: ast.Module = ast.Module(body=node.body)
        class_cfg: CFG = CFGVisitor().build(node.name, class_body)
        self.cfg.class_cfgs[node.name] = class_cfg

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
                        self.cfg.flows.add((prev_bid, next_bid))
                        self.cfg.edges.pop((block.bid, next_bid), None)
                        next_block.remove_from_prev(block.bid)
                        if (block.bid, next_block.bid) in self.cfg.flows:
                            self.cfg.flows.remove((block.bid, next_block.bid))
                    self.cfg.edges.pop((prev_bid, block.bid), None)
                    prev_block.remove_from_next(block.bid)
                    if (prev_block.bid, block.bid) in self.cfg.flows:
                        self.cfg.flows.remove((prev_block.bid, block.bid))

                block.prev.clear()
                for next_bid in block.next:
                    self.remove_empty_blocks(self.cfg.blocks[next_bid], visited)
                block.next.clear()

            else:
                for next_bid in block.next:
                    self.remove_empty_blocks(self.cfg.blocks[next_bid], visited)

    def combine_conditions(self, node_list: List[ast.expr]) -> ast.expr:
        return (
            node_list[0]
            if len(node_list) == 1
            else ast.BoolOp(op=ast.And(), values=node_list)
        )

    def generic_visit(self, node):
        if type(node) in [ast.AsyncFunctionDef]:
            self.add_stmt(self.curr_block, node)
            self.add_FuncCFG(node)
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

    def populate_body_to_next_bid(self, body_list, to_bid: int) -> None:
        for child in body_list:
            self.visit(child)
        if not self.curr_block.next:
            self.add_edge(self.curr_block.bid, to_bid)

    def populate_body(self, body_list):
        for child in body_list:
            self.visit(child)

    def visit_Module(self, node: ast.Module) -> None:
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.add_stmt(self.curr_block, node)
        self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.add_stmt(self.curr_block, node)
        new_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, new_block.bid)
        self.curr_block = new_block

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # new_func_block: FuncBlock = self.new_func_block(self.curr_block.bid)
        # self.curr_block = new_func_block
        self.add_stmt(self.curr_block, node)
        self.add_FuncCFG(node)

        self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.add_stmt(self.curr_block, node)
        self.add_ClassCFG(node)

        self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

    def visit_Assign(self, node: ast.Assign) -> None:
        node_value_type = type(node.value)
        print(astor.to_source(node))
        if node_value_type == ast.Call and any(type(arg) == ast.Call for arg in node.value.args):
            arg_sequence = []
            arg_name_sequence = []
            for arg_expr in node.value.args:
                if type(arg_expr) not in [ast.Name, ast.Num]:
                    tmp_var: str = randoms.RandomVariableName.gen_random_name()
                    assign1 = ast.Assign(
                        targets=[ast.Name(id=tmp_var, ctx=ast.Store())],
                        value=arg_expr
                    )
                    arg_sequence.append(assign1)
                    arg_name = ast.Name(
                        id=tmp_var,
                        ctx=ast.Load()
                    )
                    arg_name_sequence.append(arg_name)
                else:
                    arg_name_sequence.append(arg_expr)
            assign2 = ast.Assign(
                targets=node.targets,
                value=ast.Call(
                    func=node.value.func,
                    args=arg_name_sequence,
                    keywords=node.value.keywords
                )
            )
            arg_sequence.append(assign2)
            self.populate_body(arg_sequence)
        elif node_value_type in [ast.ListComp, ast.SetComp, ast.DictComp]:
            tmp_var: str = randoms.RandomVariableName.gen_random_name()
            new_value = None
            if node_value_type == ast.ListComp:
                new_value = ast.List(elts=[], ctx=ast.Load())
                self.list_comp_stack.append(tmp_var)
            elif node_value_type == ast.SetComp:
                new_value = ast.Call(args=[], func=ast.Name(id='set', ctx=ast.Load()), keywords=[])
                self.set_comp_stack.append(tmp_var)
            elif node_value_type == ast.DictComp:
                new_value = ast.Dict(keys=[], values=[])
                self.dict_comp_stack.append(tmp_var)
            self.add_stmt(self.curr_block, ast.Assign(
                targets=[ast.Name(id=tmp_var, ctx=ast.Store)],
                value=new_value
            ))
            self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)
            self.visit(node.value)
            old_name: str = None
            if node_value_type == ast.ListComp:
                old_name: str = self.list_comp_stack[-1]
                self.list_comp_stack.pop()
            elif node_value_type == ast.SetComp:
                old_name: str = self.set_comp_stack[-1]
                self.set_comp_stack.pop()
            elif node_value_type == ast.DictComp:
                old_name: str = self.dict_comp_stack[-1]
                self.dict_comp_stack.pop()
            self.add_stmt(self.curr_block,
                          ast.Assign(targets=node.targets,
                                     value=ast.Name(id=old_name, ctx=ast.Load())))
            self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)
        elif node_value_type == ast.GeneratorExp:
            gen_name: str = randoms.RandomGeneratorName.gen_generator_name()
            self.gen_exp_stack.append(gen_name)
            self.visit(node.value)
            self.gen_exp_stack.pop()
            new_assign: ast.Assign = ast.Assign(
                targets=node.targets,
                value=ast.Call(
                    func=ast.Name(id=gen_name, ctx=ast.Load()),
                    args=[],
                    keywords=[]
                )
            )
            self.visit(new_assign)
        else:
            self.add_stmt(self.curr_block, node)
            self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

    def visit_Call(self, node: ast.Call) -> None:
        # if type(node.func) == ast.Lambda:
        #     self.lambdaReg = ("Anonymous Function", node.func)
        #     self.generic_visit(node)
        if any(type(arg) == ast.Call for arg in node.args):
            arg_sequence = []
            arg_name_sequence = []
            for arg_expr in node.args:
                if type(arg_expr) not in [ast.Name, ast.Num]:
                    tmp_var: str = randoms.RandomVariableName.gen_random_name()
                    assign1 = ast.Assign(
                        targets=[ast.Name(id=tmp_var, ctx=ast.Store())],
                        value=arg_expr
                    )
                    arg_sequence.append(assign1)
                    arg_name = ast.Name(
                        id=tmp_var,
                        ctx=ast.Load()
                    )
                    arg_name_sequence.append(arg_name)
                else:
                    arg_name_sequence.append(arg_expr)
            call = ast.Call(
                func=node.func,
                args=arg_name_sequence,
                keywords=node.keywords
            )
            arg_sequence.append(call)
            self.populate_body(arg_sequence)
        else:
            # call_block: CallBlock = self.new_call_block(self.curr_block.bid)
            call_block = self.curr_block
            # self.curr_block = call_block
            self.add_stmt(self.curr_block, node)

            self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

            # self.curr_block.calls.append(self.get_function_name(node.func))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.add_stmt(self.curr_block, node)
        self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

    def visit_If(self, node: ast.If) -> None:
        # Add the If statement at the end of the current block.
        self.add_stmt(self.curr_block, node)

        # Create a block for the code after the if-else.
        after_if_block: BasicBlock = self.new_block()

        # Create a new block for the body of the if.
        if_body_block: BasicBlock = self.add_edge(
            self.curr_block.bid, self.new_block().bid
        )

        # New block for the body of the else if there is an else clause.
        if node.orelse:
            self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

            # Visit the children in the body of the else to populate the block.
            self.populate_body_to_next_bid(node.orelse, after_if_block.bid)
        else:
            self.add_edge(self.curr_block.bid, after_if_block.bid)
        # Visit children to populate the if block.
        self.curr_block: BasicBlock = if_body_block
        self.populate_body_to_next_bid(node.body, after_if_block.bid)

        # Continue building the CFG in the after-if block.
        self.curr_block: BasicBlock = after_if_block

    def visit_For(self, node: ast.For) -> None:

        if type(node.iter) in [ast.ListComp, ast.SetComp, ast.DictComp]:
            tmp_var: str = randoms.RandomVariableName.gen_random_name()
            new_assign: ast.Assign = ast.Assign(
                targets=[ast.Name(id=tmp_var, ctx=ast.Store())],
                value=node.iter
            )
            new_for: ast.For = ast.For(
                target=node.target,
                iter=ast.Name(id=tmp_var, ctx=ast.Load()),
                body=node.body,
                orelse=node.orelse
            )
            self.generic_visit(ast.Module(body=[new_assign, new_for]))
            return
        else:
            loop_guard = self.add_loop_block()
            self.curr_block = loop_guard
            self.add_stmt(self.curr_block, node)
            self.loop_guard_stack.append(loop_guard)

            # self.visit(node.iter)
            logging.debug('Current iter: %s', astor.to_source(node.iter))

            # New block for the body of the for-loop.
            for_block: BasicBlock = self.new_block()
            self.add_edge(self.curr_block.bid, for_block.bid)
            after_for_block: BasicBlock = self.new_block()
            self.add_edge(self.curr_block.bid, after_for_block.bid)
            self.loop_stack.append(after_for_block)
            if not node.orelse:
                # Block of code after the for loop.
                # self.add_edge(self.curr_block.bid, after_for_block.bid)

                # self.loop_stack.append(after_for_block)
                self.curr_block = for_block
                self.populate_body_to_next_bid(node.body, loop_guard.bid)
            else:
                # Block of code after the for loop.
                or_else_block: BasicBlock = self.new_block()
                self.add_edge(self.curr_block.bid, or_else_block.bid)

                # self.loop_stack.append(after_for_block)
                self.curr_block = for_block
                self.populate_body_to_next_bid(node.body, loop_guard.bid)

                self.curr_block = or_else_block
                self.populate_body_to_next_bid(node.orelse, after_for_block.bid)

            # Continue building the CFG in the after-for block.
            self.curr_block = after_for_block
            self.loop_stack.pop()
            self.loop_guard_stack.pop()

    # ignore the case when using set or dict comprehension or generator expression
    # but the result is not assigned to a variable
    def visit_Expr(self, node: ast.Expr) -> None:

        # we don't care about the node: ast.Expr itself. We care about the content of node.value.
        # if type(node.value) == ast.Call and any(type(arg) == ast.Call for arg in node.value.args):
        #     new_expr_sequence = []
        #     new_args = []
        #
        #     for arg in node.value.args:
        #         if type(arg) == ast.Call:
        #             tmp_var: str = randoms.RandomVariableName.gen_random_name()
        #             new_assign: ast.Assign = ast.Assign(
        #                 targets=[ast.Name(id=tmp_var, ctx=ast.Store())],
        #                 value=ast.Expr(value=arg)
        #             )
        #             new_expr_sequence.append(new_assign)
        #             new_args.append(ast.Name(id=tmp_var, ctx=ast.Load()))
        #         else:
        #             new_args.append(arg)
        #
        #     new_expr: ast.Expr = ast.Expr(
        #         value=ast.Call(
        #             args=new_args,
        #             func=node.value.func,
        #             keywords=node.value.keywords
        #         )
        #     )
        #     new_expr_sequence.append(new_expr)
        #     new_module: ast.Module = ast.Module(body=new_expr_sequence)
        #     self.generic_visit(new_module)
        # else:
        # self.visit(node.value)
        self.generic_visit(node)
        # self.add_stmt(self.curr_block, node)
        # self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

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

            self.populate_body_to_next_bid(node.body, loop_guard.bid)
        else:
            or_else_block: BasicBlock = self.new_block()
            self.add_edge(self.curr_block.bid, or_else_block.bid)
            self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)
            self.populate_body_to_next_bid(node.body, loop_guard.bid)

            self.curr_block = or_else_block
            self.populate_body_to_next_bid(node.orelse, after_while_block.bid)

        # Continue building the CFG in the after-while block.
        self.curr_block = after_while_block
        self.loop_stack.pop()
        self.loop_guard_stack.pop()

    def visit_Break(self, node: ast.Break) -> None:
        assert len(self.loop_stack), "Found break not inside loop"
        self.add_stmt(self.curr_block, node)
        self.add_edge(self.curr_block.bid, self.loop_stack[-1].bid)

    def visit_Return(self, node: ast.Return) -> None:
        if type(node.value) == ast.IfExp:
            self.ifExp = True
            self.generic_visit(node)
            self.ifExp = False
        else:
            self.add_stmt(self.curr_block, node)
            self.cfg.final_blocks.append(self.curr_block)
        self.curr_block = self.new_block()

    def visit_Pass(self, node: ast.Pass) -> None:
        self.add_stmt(self.curr_block, node)
        self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

    def visit_Continue(self, node: ast.Continue):
        self.add_stmt(self.curr_block, node)
        assert self.loop_guard_stack
        self.add_edge(self.curr_block.bid, self.loop_guard_stack[-1].bid)

    def visit_Assert(self, node):
        self.add_stmt(self.curr_block, node)
        self.cfg.final_blocks.append(self.curr_block)
        self.curr_block = self.add_edge(self.curr_block.bid, self.new_block().bid)

    # def visit_Await(self, node):
    #     afterawait_block = self.new_block()
    #     self.add_edge(self.curr_block.bid, afterawait_block.bid)
    #     self.generic_visit(node)
    #     self.curr_block = afterawait_block
    #
    def _visit_DictComp(self, key: ast.expr, value: ast.expr, generators):
        if not generators:
            if self.dict_comp_stack[-1]:
                return [
                    ast.Assign(
                        targets=[
                            ast.Subscript(
                                value=ast.Name(id=self.dict_comp_stack[-1], ctx=ast.Load()),
                                slice=ast.Index(value=key),
                                ctx=ast.Store(),
                            )
                        ],
                        value=value,
                    )
                ]
        else:
            return [
                ast.For(
                    target=generators[0].target,
                    iter=generators[0].iter,
                    body=[
                        ast.If(
                            test=self.combine_conditions(generators[0].ifs),
                            body=self._visit_DictComp(key, value, generators[1:]),
                            orelse=[]
                        )
                    ]
                    if generators[0].ifs
                    else self._visit_DictComp(key, value, generators[1:]),
                    orelse=[]
                )
            ]

    def visit_DictComp(self, node: ast.DictComp) -> None:
        generated_for = self._visit_DictComp(node.key, node.value, node.generators)
        self.generic_visit(ast.Module(generated_for))

    def _visit_GeneratorExp(self, node: ast.GeneratorExp, generators: List[ast.comprehension]):
        if not generators:
            return [ast.Expr(value=ast.Yield(value=node.elt))]
        else:
            return [
                ast.For(
                    target=generators[0].target,
                    iter=generators[0].iter,
                    body=[
                        ast.If(
                            test=self.combine_conditions(generators[0].ifs),
                            body=self._visit_GeneratorExp(node, generators[1:]),
                            orelse=[],
                        )
                    ]
                    if generators[0].ifs
                    else self._visit_GeneratorExp(node, generators[1:]),
                    orelse=[],
                )
            ]

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        generator_function: ast.FunctionDef = ast.FunctionDef(
            name=self.gen_exp_stack[-1],
            args=ast.arguments(args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
            body=self._visit_GeneratorExp(node, node.generators),
            decorator_list=[],
            returns=None
        )
        logging.debug(astor.to_source(generator_function))
        self.visit(generator_function)

    def _visit_IfExp(self, node: ast.IfExp) -> List[ast.If]:
        return [
            ast.If(
                test=node.test,
                body=self._visit_IfExp(node.body)
                if type(node.body) == ast.IfExp
                else [ast.Return(value=node.body)],
                orelse=self._visit_IfExp(node.orelse)
                if type(node.orelse) == ast.IfExp
                else [ast.Return(value=node.orelse)],
            )
        ]

    def visit_IfExp(self, node: ast.IfExp) -> None:
        if self.ifExp:
            module: ast.Module = ast.Module(body=self._visit_IfExp(node))
            self.generic_visit(module)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        logging.debug('Enter visit_Lambda')
        self.add_FuncCFG()

    def _visit_ListComp(self, elt: ast.expr, generators: List[ast.comprehension]):
        if not generators:
            if self.list_comp_stack[-1]:
                return [
                    ast.Expr(
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id=self.list_comp_stack[-1], ctx=ast.Load()),
                                attr="append",
                                ctx=ast.Load(),
                            ),
                            args=[elt],
                            keywords=[],
                        )
                    )
                ]
            else:
                return [ast.Expr(value=elt)]
        else:
            return [
                ast.For(
                    target=generators[0].target,
                    iter=generators[0].iter,
                    body=[
                        ast.If(
                            test=self.combine_conditions(generators[0].ifs),
                            body=self._visit_ListComp(elt, generators[1:]),
                            orelse=[],
                        )
                    ]
                    if generators[0].ifs
                    else self._visit_ListComp(elt, generators[1:]),
                    orelse=[],
                )
            ]

    def visit_ListComp(self, node: ast.ListComp) -> None:

        generated_for = self._visit_ListComp(node.elt, node.generators)
        self.generic_visit(ast.Module(generated_for))

    # def visit_Raise(self, node):
    #     self.add_stmt(self.curr_block, node)
    #     self.curr_block = self.new_block()
    #
    def _visit_SetComp(self, elt: ast.expr, generators: List[ast.comprehension]):
        if not generators:
            if self.set_comp_stack[-1]:
                return [
                    ast.Expr(
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id=self.set_comp_stack[-1], ctx=ast.Load()),
                                attr="add",
                                ctx=ast.Load(),
                            ),
                            args=[elt],
                            keywords=[]
                        )
                    )
                ]
            else:
                return [ast.Expr(value=elt)]
        else:
            return [
                ast.For(
                    target=generators[0].target,
                    iter=generators[0].iter,
                    body=[
                        ast.If(
                            test=self.combine_conditions(generators[0].ifs),
                            body=self._visit_SetComp(elt, generators[1:]),
                            orelse=[],
                        )
                    ]
                    if generators[0].ifs
                    else self._visit_SetComp(elt, generators[1:]),
                    orelse=[],
                )
            ]

    def visit_SetComp(self, node: ast.SetComp) -> None:
        generated_for = self._visit_SetComp(node.elt, node.generators)
        self.generic_visit(ast.Module(body=generated_for))

    def visit_Try(self, node: ast.Try) -> None:
        loop_guard = self.add_loop_block()
        self.curr_block = loop_guard
        self.add_stmt(
            loop_guard, ast.Try(body=[], handlers=[], orelse=[], finalbody=[])
        )

        after_try_block = self.new_block()
        self.add_stmt(after_try_block, ast.Name(id="handle errors", ctx=ast.Load()))
        self.populate_body_to_next_bid(node.body, after_try_block.bid)

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
                self.populate_body_to_next_bid(handler.body, after_handler_block.bid)
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
            self.populate_body_to_next_bid(node.orelse, after_else_block.bid)
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
            self.populate_body_to_next_bid(node.finalbody, after_finally_block.bid)
            self.curr_block = after_finally_block
        else:
            self.add_edge(after_try_block.bid, finally_block.bid)

    def visit_Yield(self, node: ast.Yield) -> None:
        self.add_stmt(self.curr_block, node)
        new_block: BasicBlock = self.new_block()
        self.add_edge(self.curr_block.bid, new_block.bid)
        self.curr_block = new_block

# if __name__ == "__main__":
#     filename = sys.argv[1]
#     file = open(filename, "r")
#     source = file.read()
#     file.close()
#
#     comments_cleaner = comments.CommentsCleaner(source)
#     comments_cleaner.remove_comments_and_docstrings()
#     comments_cleaner.format_code()
#     logging.debug(comments_cleaner.source)
#
#     cfg = CFGVisitor().build(filename, ast.parse(comments_cleaner.source))
#     logging.debug('flows: %s', sorted(cfg.flows))
#     logging.debug('edges: %s', sorted(cfg.edges.keys()))
#     cfg.show()