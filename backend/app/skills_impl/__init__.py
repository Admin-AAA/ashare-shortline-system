"""命令式（Python）策略实现的存放包（任务 3.5，需求 4.6）。

落点说明——为什么新建 ``app/skills_impl/`` 而不是塞进 ``engine/``：
    ``engine/`` 是策略评估的「原语」深模块聚集地（``evaluator.py`` / ``skill_loader.py`` /
    ``strategy.py`` / ``imperative_strategy.py`` / ``strategy_runner.py`` /
    ``signal.py``）——这些模块回答的是「命令式策略这个概念本身怎么被装配、怎么被
    模板方法约束」。而本包里的类回答的是完全不同的问题：「某一个具体命令式策略的
    业务打分逻辑是什么」。二者不应混在一起，理由与声明式策略的 ``strategy.yaml``
    不放在 ``engine/`` 而放在 ``backend/skills/<id>/`` 完全一致——``engine/`` 放
    「策略框架」，具体策略的业务实现（无论是 yaml 还是 Python 类）放在框架之外。

    命令式策略的 Python 类必须能被 ``app.engine.strategy.instantiate_strategy`` 用
    ``importlib.import_module(module_path)`` 动态 import（``manifest.json`` 里的
    ``impl_class`` 是形如 ``"module.path:ClassName"`` 的点分路径）。要让这条路径在
    生产环境（uvicorn 从 ``backend/`` 目录启动，``app`` 包已在 ``sys.path``）与测试
    环境（``conftest.py`` 把 ``backend/`` 插入 ``sys.path``）下都稳定可达，最简单
    可靠的选择就是把具体策略类放在 ``app.*`` 命名空间下的一个新子包里，与
    ``app.engine`` / ``app.services`` 同级——不依赖任何「测试专用 sys.path 技巧」
    （3.4 的 ``tests/_impl_class_fixtures.py`` 那种手法是测试夹具的权宜之计，不适合
    作为生产命令式策略的存放约定）。

    每新增一个命令式策略，只需在本包下新增一个模块（一个类），在对应
    ``backend/skills/<id>/manifest.json`` 里把 ``impl_class`` 指向
    ``"app.skills_impl.<module>:<ClassName>"`` 即可被 ``load_skills`` /
    ``instantiate_strategy`` / ``build_registry_from_skills`` 零改动纳入分发——
    这正是任务 3.5 要验证的设计目标（需求 4.6）。
"""
