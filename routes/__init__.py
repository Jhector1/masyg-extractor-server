from flask import Flask

def register_blueprints(app: Flask):
    from .data_extractor_routes import file_extractor
    from .payment_routes import payment
    from .user_routes import user
    # from .products import products_bp
    # from .orders import orders_bp

    # Register all blueprints
    app.register_blueprint(file_extractor, url_prefix='/api')
    app.register_blueprint(payment, url_prefix='/api')
    app.register_blueprint(user, url_prefix='/api')
    # app.register_blueprint(orders_bp, url_prefix='/api')
