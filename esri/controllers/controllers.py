# from odoo import http


# class Esri(http.Controller):
#     @http.route('/esri/esri', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/esri/esri/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('esri.listing', {
#             'root': '/esri/esri',
#             'objects': http.request.env['esri.esri'].search([]),
#         })

#     @http.route('/esri/esri/objects/<model("esri.esri"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('esri.object', {
#             'object': obj
#         })

