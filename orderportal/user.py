"OrderPortal: User and login pages."

from __future__ import unicode_literals, print_function, absolute_import

import tornado.web

import orderportal
from orderportal import constants
from orderportal import settings
from orderportal import utils
from orderportal import saver
from orderportal.requesthandler import RequestHandler


class UserSaver(saver.Saver):
    doctype = constants.USER

    def set_email(self, email):
        view = self.db.view('user/email')
        if len(list(view[email])) != 0:
            raise ValueError('email address already in use')
        self['email'] = email

    def set_password(self, new):
        self['password'] = utils.hashed_password(new)

    def set_activation(self):
        self['password'] = None
        self['activation'] = utils.get_iuid()


class Users(RequestHandler):
    "Users list page."

    @tornado.web.authenticated
    def get(self):
        self.check_admin()
        view = self.db.view('user/email', include_docs=True)
        users = [self.get_presentable(r.doc) for r in view]
        self.render('users.html', title='Users', users=users)


class User(RequestHandler):
    "User page."

    @tornado.web.authenticated
    def get(self, email):
        user = self.get_user(email)
        self.check_owner_or_staff(user)
        self.render('user.html',
                    title="User {0}".format(user['email']),
                    user=user)


class Login(RequestHandler):
    "Login to a user account. Set a secure cookie."

    def post(self):
        "Login to a user account. Set a secure cookie."
        self.check_xsrf_cookie()
        try:
            email = self.get_argument('email')
            password = self.get_argument('password')
        except tornado.web.MissingArgumentError:
            raise tornado.web.HTTPError(403, reason='missing email or password')
        try:
            user = self.get_user(email)
        except tornado.web.HTTPError:
            raise tornado.web.HTTPError(404, reason='no such user')
        if not utils.hashed_password(password) == user.get('password'):
            raise tornado.web.HTTPError(400, reason='invalid password')
        if not user.get('status') == constants.ENABLED:
            raise tornado.web.HTTPError(400, reason='disabled user account')
        self.set_secure_cookie(constants.USER_COOKIE, user['email'])
        with UserSaver(doc=user, rqh=self) as saver:
            saver['login'] = utils.timestamp() # Set login session.
        self.redirect(self.reverse_url('home'))


class Logout(RequestHandler):
    "Logout; unset the secure cookie, and invalidate login session."

    def post(self):
        self.check_xsrf_cookie()
        self.set_secure_cookie(constants.USER_COOKIE, '')
        with UserSaver(doc=user, rqh=self) as saver:
            saver['login'] = None
        self.redirect(self.reverse_url('home'))


class Reset(RequestHandler):
    "Reset the password of a user account."

    SUBJECT = "The password for your {} portal account has been reset"
    TEXT = """The password for your account {} in the {} portal has been reset.
Please got to {} to set your password.
The code required to set your password is "{}".
"""

    def get(self):
        email = self.current_user and self.current_user.get('email')
        self.render('reset.html', email=email, title='Reset your password')

    def post(self):
        self.check_xsrf_cookie()
        user = self.get_user(self.get_argument('email'))
        with UserSaver(doc=user, rqh=self) as saver:
            saver['password'] = None
            saver['code'] = utils.get_iuid()
            saver['login'] = None # Invalidate login session.
        url = self.absolute_reverse_url('password',
                                        email=user['email'],
                                        code=user['code'])
        self.send_email(user['email'],
                        self.SUBJECT.format(settings['FACILITY_NAME']),
                        self.TEXT.format(user['email'],
                                         settings['FACILITY_NAME'],
                                         url,
                                         user['code']))
        self.see_other(self.reverse_url('password'))


class Password(RequestHandler):
    "Set the password of a user account; requires a code."

    def get(self):
        self.render('password.html',
                    title='Set your password',
                    email=self.get_argument('email', default=''),
                    code=self.get_argument('code', default=''))

    def post(self):
        self.check_xsrf_cookie()
        user = self.get_user(self.get_argument('email'))
        if user.get('code') != self.get_argument('code'):
            raise tornado.web.HTTPError(400, reason='invalid email or code')
        password = self.get_argument('password')
        if not self.valid_password(password):
            raise tornado.web.HTTPError(400, reason='invalid password')
        if password != self.get_argument('confirm_password'):
            raise tornado.web.HTTPError(400, reason='passwords not the same')
        with UserSaver(doc=user, rqh=self) as saver:
            saver.set_password(password)
            saver['code'] = None
            saver['login'] = utils.timestamp() # Set login session.
        self.set_secure_cookie(constants.USER_COOKIE, user['email'])
        self.redirect(self.reverse_url('home'))
            

    def valid_password(self, password):
        return len(password) >= 6


class Register(RequestHandler):
    "Register a new user account."

    def post(self):
        self.check_xsrf_cookie()
        with UserSaver(rqh=self) as saver:
            try:
                key = 'email'
                email = self.get_argument(key)
                if not email: raise ValueError
                if not constants.EMAIL_RX.match(email): raise ValueError
                try:
                    self.get_user(email)
                except tornado.web.HTTPError:
                    pass
                else:
                    reason = 'email address already in use'
                    raise tornado.web.HTTPError(409, reason=reason)
                saver[key] = email
                for key in ['first_name', 'last_name', 'university']:
                    value = self.get_argument(key)
                    if not value: raise ValueError
                    saver[key] = value
            except (tornado.web.MissingArgumentError, ValueError):
                reason = "invalid {} value provided".format(key)
                raise tornado.web.HTTPError(400, reason=reason)
            for key in ['department', 'address', 'phone']:
                saver[key] = self.get_argument(key, default=None)
            saver['owner'] = email
            saver['role'] = constants.USER
            saver['status'] = constants.PENDING
            saver['password'] = None
        self.see_other(self.reverse_url('password'))


class UserEnable(RequestHandler):
    "Enable the user; from status pending or disabled."

    SUBJECT = "Your {} portal account has been enabled"
    TEXT = """Your account {} in the {} portal has been enabled.
Please got to {} to set your password.
"""

    @tornado.web.authenticated
    def post(self, email):
        self.check_xsrf_cookie()
        user = self.get_user(email)
        self.check_admin()
        with UserSaver(user, rqh=self) as saver:
            saver['code'] = utils.get_iuid()
            saver['password'] = None
            saver['status'] = constants.ENABLED
        text = "Your account {} in the {} portal has been enabled."
        url = self.absolute_reverse_url('password',
                                        email=email,
                                        code=user['code'])
        self.send_email(user['email'],
                        self.SUBJECT.format(settings['FACILITY_NAME']),
                        self.TEXT.format(email, settings['FACILITY_NAME'], url))
        self.see_other(self.reverse_url('user', email))


class UserDisable(RequestHandler):
    "Disable the user; from status pending or enabled."

    @tornado.web.authenticated
    def post(self, email):
        self.check_xsrf_cookie()
        user = self.get_user(email)
        self.check_admin()
        with UserSaver(user, rqh=self) as saver:
            saver['password'] = None
            saver['status'] = constants.DISABLED
        self.see_other(self.reverse_url('user', email))
