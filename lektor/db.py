import re
import os
import uuid
import errno
import hashlib
import operator
import functools
import posixpath

from itertools import islice

from jinja2 import Undefined, is_undefined
from jinja2.utils import LRUCache

from lektor import metaformat
from lektor.utils import sort_normalize_string
from lektor.sourceobj import SourceObject
from lektor.context import get_ctx
from lektor.datamodel import load_datamodels, load_flowblocks
from lektor.thumbnail import make_thumbnail
from lektor.assets import Directory


_slashes_re = re.compile(r'/+')


def cleanup_path(path):
    return '/' + _slashes_re.sub('/', path.strip('/'))


def to_os_path(path):
    return path.strip('/').replace('/', os.path.sep)


def _require_ctx(record):
    ctx = get_ctx()
    if ctx is None:
        raise RuntimeError('This operation requires a context but none was '
                           'on the stack.')
    if ctx.pad is not record.pad:
        raise RuntimeError('The context on the stack does not match the '
                           'pad of the record.')
    return ctx


@functools.total_ordering
class _CmpHelper(object):

    def __init__(self, value, reverse):
        self.value = value
        self.reverse = reverse

    @staticmethod
    def coerce(a, b):
        if isinstance(a, basestring) and isinstance(b, basestring):
            return sort_normalize_string(a), sort_normalize_string(b)
        if type(a) is type(b):
            return a, b
        if isinstance(a, (int, long, float)):
            try:
                return a, type(a)(b)
            except (ValueError, TypeError, OverflowError):
                pass
        if isinstance(b, (int, long, float)):
            try:
                return type(b)(a), b
            except (ValueError, TypeError, OverflowError):
                pass
        return a, b

    def __eq__(self, other):
        a, b = self.coerce(self.value, other.value)
        return a == b

    def __lt__(self, other):
        a, b = self.coerce(self.value, other.value)
        if self.reverse:
            return b < a
        return a < b


def _auto_wrap_expr(value):
    if isinstance(value, _Expr):
        return value
    return _Literal(value)


class _Expr(object):

    def __eval__(self, record):
        return record

    def __eq__(self, other):
        return _BinExpr(self, _auto_wrap_expr(other), operator.eq)

    def __ne__(self, other):
        return _BinExpr(self, _auto_wrap_expr(other), operator.ne)

    def __and__(self, other):
        return _BinExpr(self, _auto_wrap_expr(other), operator.and_)

    def __or__(self, other):
        return _BinExpr(self, _auto_wrap_expr(other), operator.or_)

    def __gt__(self, other):
        return _BinExpr(self, _auto_wrap_expr(other), operator.gt)

    def __ge__(self, other):
        return _BinExpr(self, _auto_wrap_expr(other), operator.ge)

    def __lt__(self, other):
        return _BinExpr(self, _auto_wrap_expr(other), operator.lt)

    def __le__(self, other):
        return _BinExpr(self, _auto_wrap_expr(other), operator.le)

    def contains(self, item):
        return _ContainmentExpr(self, _auto_wrap_expr(item))

    def startswith(self, other):
        return _BinExpr(self, _auto_wrap_expr(other),
            lambda a, b: unicode(a).lower().startswith(unicode(b).lower()))

    def endswith(self, other):
        return _BinExpr(self, _auto_wrap_expr(other),
            lambda a, b: unicode(a).lower().endswith(unicode(b).lower()))

    def startswith_cs(self, other):
        return _BinExpr(self, _auto_wrap_expr(other),
                        lambda a, b: unicode(a).startswith(unicode(b)))

    def endswith_cs(self, other):
        return _BinExpr(self, _auto_wrap_expr(other),
                        lambda a, b: unicode(a).endswith(unicode(b)))


class _Literal(_Expr):

    def __init__(self, value):
        self.__value = value

    def __eval__(self, record):
        return self.__value


class _BinExpr(_Expr):

    def __init__(self, left, right, op):
        self.__left = left
        self.__right = right
        self.__op = op

    def __eval__(self, record):
        return self.__op(
            self.__left.__eval__(record),
            self.__right.__eval__(record)
        )


class _ContainmentExpr(_Expr):

    def __init__(self, seq, item):
        self.__seq = seq
        self.__item = item

    def __eval__(self, record):
        seq = self.__seq.__eval__(record)
        item = self.__item.__eval__(record)
        if isinstance(item, Record):
            item = item['_id']
        return item in seq


class _RecordQueryField(_Expr):

    def __init__(self, field):
        self.__field = field

    def __eval__(self, record):
        try:
            return record[self.__field]
        except KeyError:
            return Undefined(obj=record, name=self.__field)


class _RecordQueryProxy(object):

    def __getattr__(self, name):
        if name[:2] != '__':
            return _RecordQueryField(name)
        raise AttributeError(name)

    def __getitem__(self, name):
        try:
            return self.__getattr__(name)
        except AttributeError:
            raise KeyError(name)


F = _RecordQueryProxy()


class Record(SourceObject):
    source_classification = 'record'

    def __init__(self, pad, data):
        SourceObject.__init__(self, pad)
        self._data = data
        self._fast_source_hash = None

    record_classification = 'record'

    @property
    def datamodel(self):
        """Returns the data model for this record."""
        try:
            return self.pad.db.datamodels[self._data['_model']]
        except LookupError:
            # If we cannot find the model we fall back to the default one.
            return self.pad.db.default_model

    @property
    def is_exposed(self):
        """This is `true` if the record is exposed, `false` otherwise.  If
        a record does not set this itself, it's inherited from the parent
        record.  If no record has this defined in the direct line to the
        root, then a default of `True` is assumed.
        """
        expose = self._data['_expose']
        if is_undefined(expose):
            if not self.datamodel.expose:
                return False
            if self.parent is None:
                return True
            return self.parent.is_exposed
        return expose

    @property
    def is_hidden(self):
        """Hidden is similar to exposed but it does not inherit down to
        children.  Hidden children generally completely disappear from all
        handling.
        """
        return self._data['_hidden'] or False

    @property
    def is_visible(self):
        """Indicates that this page is actually visible.  That means it is
        exposed and not hidden.
        """
        return self.is_exposed and not self.is_hidden

    @property
    def record_label(self):
        """The generic record label."""
        rv = self.datamodel.format_record_label(self)
        if rv:
            return rv
        if not self['_id']:
            return '(Index)'
        return self['_id'].replace('-', ' ').replace('_', ' ').title()

    @property
    def url_path(self):
        """The target path where the record should end up."""
        bits = []
        node = self
        while node is not None:
            bits.append(node['_slug'])
            node = node.parent
        bits.reverse()
        return '/' + '/'.join(bits).strip('/')

    def get_sort_key(self, fields):
        """Returns a sort key for the given field specifications specific
        for the data in the record.
        """
        rv = [None] * len(fields)
        for idx, field in enumerate(fields):
            if field[:1] == '-':
                field = field[1:]
                reverse = True
            else:
                field = field.lstrip('+')
                reverse = False
            rv[idx] = _CmpHelper(self._data.get(field), reverse)
        return rv

    def to_dict(self):
        """Returns a clone of the internal data dictionary."""
        return dict(self._data)

    def to_json(self):
        """Similar to :meth:`to_dict` but the return value will be valid
        JSON.
        """
        return self.datamodel.to_json(self._data, pad=self.pad)

    def iter_fields(self):
        """Iterates over all fields and values."""
        return self._data.iteritems()

    def iter_record_path(self):
        """Iterates over all records that lead up to the current record."""
        rv = []
        node = self
        while node is not None:
            rv.append(node)
            node = node.parent
        return reversed(rv)

    def __contains__(self, name):
        return name in self._data and not is_undefined(self._data[name])

    def __getitem__(self, name):
        return self._data[name]

    def __setitem__(self, name, value):
        self.pad.cache.persist_if_cached(self)
        self._data[name] = value

    def __delitem__(self, name):
        self.pad.cache.persist_if_cached(self)
        del self._data[name]

    def __repr__(self):
        return '<%s model=%r path=%r>' % (
            self.__class__.__name__,
            self['_model'],
            self['_path'],
        )


class Page(Record):
    """This represents a loaded record."""

    record_classification = 'page'

    @property
    def source_filename(self):
        return posixpath.join(self.pad.db.to_fs_path(self['_path']),
                              'contents.lr')

    def _iter_dependent_filenames(self):
        yield self.source_filename

    @property
    def url_path(self):
        url_path = Record.url_path.__get__(self)
        if url_path[-1:] != '/':
            url_path += '/'
        return url_path

    def is_child_of(self, path):
        this_path = cleanup_path(self['_path']).split('/')
        crumbs = cleanup_path(path).split('/')
        return this_path[:len(crumbs)] == crumbs

    def resolve_url_path(self, url_path):
        if not url_path:
            return self

        for idx in xrange(len(url_path)):
            piece = '/'.join(url_path[:idx + 1])
            child = self.real_children.filter(F._slug == piece).first()
            if child is None:
                attachment = self.attachments.filter(F._slug == piece).first()
                if attachment is None:
                    continue
                node = attachment
            else:
                node = child

            rv = node.resolve_url_path(url_path[idx + 1:])
            if rv is not None:
                return rv

    @property
    def parent(self):
        """The parent of the record."""
        this_path = self._data['_path']
        parent_path = posixpath.dirname(this_path)
        if parent_path != this_path:
            return self.pad.get(parent_path,
                                persist=self.pad.cache.is_persistent(self))

    @property
    def all_children(self):
        """A query over all children that are not hidden."""
        repl_query = self.datamodel.get_child_replacements(self)
        if repl_query is not None:
            return repl_query
        return Query(path=self['_path'], pad=self.pad)

    @property
    def children(self):
        """Returns a query for all the children of this record.  Optionally
        a child path can be specified in which case the children of a sub
        path are queried.
        """
        return self.all_children.visible_only

    @property
    def real_children(self):
        """A query over all real children of this page.  This includes
        hidden.
        """
        if self.datamodel.child_config.replaced_with is not None:
            return iter(())
        return self.all_children

    def find_page(self, path):
        """Finds a child page."""
        return self.children.get(path)

    @property
    def attachments(self):
        """Returns a query for the attachments of this record."""
        return AttachmentsQuery(path=self['_path'], pad=self.pad)


class Attachment(Record):
    """This represents a loaded attachment."""

    record_classification = 'attachment'

    @property
    def source_filename(self):
        return self.pad.db.to_fs_path(self['_path']) + '.lr'

    @property
    def attachment_filename(self):
        return self.pad.db.to_fs_path(self['_path'])

    @property
    def parent(self):
        """The associated record for this attachment."""
        return self.pad.get(self._data['_attachment_for'],
                            persist=self.pad.cache.is_persistent(self))

    @property
    def record_label(self):
        """The generic record label."""
        rv = self.datamodel.format_record_label(self)
        if rv is not None:
            return rv
        return self['_id']

    def _iter_dependent_filenames(self):
        # We only want to yield the source filename if it actually exists.
        # For attachments it's very likely that this is not the case in
        # case no metadata was defined.
        if os.path.isfile(self.source_filename):
            yield self.source_filename
        yield self.attachment_filename


class Image(Attachment):
    """Specific class for image attachments."""

    def thumbnail(self, width, height=None):
        return make_thumbnail(_require_ctx(self),
            self.attachment_filename, self.url_path,
            width=width, height=height)


attachment_classes = {
    'image': Image,
}


class Query(object):

    def __init__(self, path, pad):
        self.path = path
        self.pad = pad
        self._include_pages = True
        self._include_attachments = False
        self._order_by = None
        self._filters = None
        self._pristine = True
        self._limit = None
        self._offset = None
        self._visible_only = False

    @property
    def self(self):
        """Returns the object this query starts out from."""
        return self.pad.get(self.path)

    def _clone(self, mark_dirty=False):
        """Makes a flat copy but keeps the other data on it shared."""
        rv = object.__new__(self.__class__)
        rv.__dict__.update(self.__dict__)
        if mark_dirty:
            rv._pristine = False
        return rv

    def _get(self, id, persist=True):
        """Low level record access."""
        return self.pad.get('%s/%s' % (self.path, id), persist=persist)

    def _iterate(self):
        """Low level record iteration."""
        for name, is_attachment in self.pad.db.iter_items(self.path):
            if not ((is_attachment == self._include_attachments) or
                    (not is_attachment == self._include_pages)):
                continue

            record = self._get(name, persist=False)
            if self._visible_only and not record.is_visible:
                continue
            for filter in self._filters or ():
                if not filter.__eval__(record):
                    break
            else:
                yield record

    def filter(self, expr):
        """Filters records by an expression."""
        rv = self._clone(mark_dirty=True)
        rv._filters = list(self._filters or ())
        rv._filters.append(expr)
        return rv

    def get_order_by(self):
        """Returns the order that should be used."""
        if self._order_by is not None:
            return self._order_by
        base_record = self.pad.get(self.path)
        if base_record is not None:
            return base_record.datamodel.child_config.order_by

    @property
    def visible_only(self):
        """Returns all visible pages."""
        rv = self._clone(mark_dirty=True)
        rv._visible_only = True
        return rv

    @property
    def with_attachments(self):
        """Includes attachments as well."""
        rv = self._clone(mark_dirty=True)
        rv._include_attachments = True
        return rv

    def first(self):
        """Loads all matching records as list."""
        return next(iter(self), None)

    def all(self):
        """Loads all matching records as list."""
        return list(self)

    def order_by(self, *fields):
        """Sets the ordering of the query."""
        rv = self._clone()
        rv._order_by = fields or None
        return rv

    def offset(self, offset):
        """Sets the ordering of the query."""
        rv = self._clone(mark_dirty=True)
        rv._offset = offset
        return rv

    def limit(self, limit):
        """Sets the ordering of the query."""
        rv = self._clone(mark_dirty=True)
        rv._limit = limit
        return rv

    def count(self):
        """Counts all matched objects."""
        rv = 0
        for item in self._iterate():
            rv += 1
        return rv

    def get(self, id):
        """Gets something by the local path.  This ignores all other
        filtering that might be applied on the query.
        """
        if not self._pristine:
            raise RuntimeError('The query object is not pristine')
        return self._get(id)

    def __nonzero__(self):
        return self.first() is not None

    def __iter__(self):
        """Iterates over all records matched."""
        iterable = self._iterate()

        order_by = self.get_order_by()
        if order_by:
            iterable = sorted(
                iterable, key=lambda x: x.get_sort_key(order_by))

        if self._offset is not None or self._limit is not None:
            iterable = islice(iterable, self._offset or 0, self._limit)

        for item in iterable:
            yield item

    def __repr__(self):
        return '<%s %r>' % (
            self.__class__.__name__,
            self.path,
        )


class AttachmentsQuery(Query):

    def __init__(self, path, pad):
        Query.__init__(self, path, pad)
        self._include_pages = False
        self._include_attachments = True

    @property
    def images(self):
        """Filters to images."""
        return self.filter(F._attachment_type == 'image')

    @property
    def videos(self):
        """Filters to videos."""
        return self.filter(F._attachment_type == 'video')

    @property
    def audio(self):
        """Filters to audio."""
        return self.filter(F._attachment_type == 'audio')


def _iter_filename_choices(fn_base):
    # the order here is important as attachments can exist without a .lr
    # file and as such need to come second or the loading of raw data will
    # implicitly say the record exists.
    yield os.path.join(fn_base, 'contents.lr'), False
    yield fn_base + '.lr', True


def _iter_datamodel_choices(datamodel_name, raw_data):
    yield datamodel_name
    if not raw_data.get('_attachment_for'):
        yield posixpath.basename(raw_data['_path']) \
            .split('.')[0].replace('-', '_').lower()
    yield 'page'
    yield 'none'


class Database(object):

    def __init__(self, env):
        self.env = env
        self.datamodels = load_datamodels(env)
        self.flowblocks = load_flowblocks(env)

    def to_fs_path(self, path):
        """Convenience function to convert a path into an file system path."""
        return os.path.join(self.env.root_path, 'content', to_os_path(path))

    def load_raw_data(self, path, cls=None):
        """Internal helper that loads the raw record data.  This performs
        very little data processing on the data.
        """
        path = cleanup_path(path)
        if cls is None:
            cls = dict

        fn_base = self.to_fs_path(path)

        rv = cls()
        for fs_path, is_attachment in _iter_filename_choices(fn_base):
            try:
                with open(fs_path, 'rb') as f:
                    for key, lines in metaformat.tokenize(f, encoding='utf-8'):
                        rv[key] = u''.join(lines)
            except IOError as e:
                if e.errno not in (errno.ENOTDIR, errno.ENOENT):
                    raise
                if not is_attachment or not os.path.isfile(fs_path[:-3]):
                    continue
                rv = {}
            rv['_path'] = path
            rv['_id'] = posixpath.basename(path)
            if is_attachment:
                rv['_attachment_for'] = posixpath.dirname(path)
            return rv

    def iter_items(self, path):
        """Iterates over all items below a path and yields them as
        tuples in the form ``(id, is_attachment)``.
        """
        fn_base = self.to_fs_path(path)

        for fs_path, is_attachment in _iter_filename_choices(fn_base):
            if not os.path.isfile(fs_path):
                continue
            # This path is actually for an attachment, which means that we
            # cannot have any items below it and will just abort with an
            # empty iterator.
            if is_attachment:
                return

            try:
                dir_path = os.path.dirname(fs_path)
                for filename in os.listdir(dir_path):
                    if self.env.is_uninteresting_source_name(filename) or \
                       filename == 'contents.lr':
                        continue
                    if os.path.isfile(os.path.join(dir_path, filename,
                                                   'contents.lr')):
                        yield filename, False
                    elif filename[-3:] != '.lr' and os.path.isfile(
                            os.path.join(dir_path, filename)):
                        yield filename, True
            except IOError as e:
                if e.errno != errno.ENOENT:
                    raise

    def list_items(self, path):
        """Like :meth:`iter_items` but returns a list."""
        return list(self.iter_items(path))

    def get_datamodel_for_raw_data(self, raw_data, pad=None):
        """Returns the datamodel that should be used for a specific raw
        data.  This might require the discovery of a parent object through
        the pad.
        """
        is_attachment = bool(raw_data.get('_attachment_for'))
        dm_name = (raw_data.get('_model') or '').strip() or None

        # Only look for a datamodel if there was not defined.
        if dm_name is None:
            parent = posixpath.dirname(raw_data['_path'])
            dm_name = None

            # If we hit the root, and there is no model defined we need
            # to make sure we do not recurse onto ourselves.
            if parent != raw_data['_path']:
                if pad is None:
                    pad = self.new_pad()
                parent_obj = pad.get(parent)
                if parent_obj is not None:
                    if is_attachment:
                        dm_name = parent_obj.datamodel.attachment_config.model
                    else:
                        dm_name = parent_obj.datamodel.child_config.model

        for dm_name in _iter_datamodel_choices(dm_name, raw_data):
            # If that datamodel exists, let's roll with it.
            datamodel = self.datamodels.get(dm_name)
            if datamodel is not None:
                return datamodel

        raise AssertionError("Did not find an appropriate datamodel.  "
                             "That should never happen.")

    def get_attachment_type(self, path):
        """Gets the attachment type for a path."""
        return self.env.config['ATTACHMENT_TYPES'].get(
            posixpath.splitext(path)[1])

    def track_record_dependency(self, record):
        ctx = get_ctx()
        if ctx is not None:
            for filename in record._iter_dependent_filenames():
                ctx.record_dependency(filename)
            if record.datamodel.filename:
                ctx.record_dependency(record.datamodel.filename)
        return record

    def postprocess_record(self, record, persist):
        # Automatically fill in slugs
        if is_undefined(record['_slug']):
            parent = record.parent
            if parent:
                slug = parent.datamodel.get_default_child_slug(record)
            else:
                slug = ''
            record['_slug'] = slug
        else:
            record['_slug'] = record['_slug'].strip('/')

        # Automatically fill in templates
        if is_undefined(record['_template']):
            record['_template'] = record.datamodel.get_default_template_name()

        # Fill in the global ID
        gid_hash = hashlib.md5()
        node = record
        while node is not None:
            gid_hash.update(node['_id'].encode('utf-8'))
            node = node.parent
        record['_gid'] = uuid.UUID(bytes=gid_hash.digest(), version=3)

        # Fill in attachment type
        if is_undefined(record['_attachment_type']):
            record['_attachment_type'] = self.get_attachment_type(
                record['_path'])

        # Automatically cache
        if persist:
            record.pad.cache.persist(record)
        else:
            record.pad.cache.remember(record)

    def get_record_class(self, datamodel, raw_data):
        """Returns the appropriate record class for a datamodel and raw data."""
        is_attachment = bool(raw_data.get('_attachment_for'))

        if not is_attachment:
            return Page

        # We need to replicate the logic from postprocess_record here so
        # that we can find the right attachment class.  Not ideal
        attachment_type = raw_data.get('_attachment_type')
        if not attachment_type:
            attachment_type = self.get_attachment_type(raw_data['_path'])
        return attachment_classes.get(attachment_type, Attachment)

    def new_pad(self):
        return Pad(self)


class Pad(object):

    def __init__(self, db):
        self.db = db
        self.cache = RecordCache(db.env.config['EPHEMERAL_RECORD_CACHE_SIZE'])

    def resolve_url_path(self, url_path, include_invisible=False):
        """Given a URL path this will find the correct record which also
        might be an attachment.  If a record cannot be found or is unexposed
        the return value will be `None`.
        """
        node = self.root

        pieces = cleanup_path(url_path).strip('/').split('/')
        if pieces == ['']:
            pieces = []

        rv = node.resolve_url_path(pieces)
        if rv is not None and (include_invisible or rv.is_exposed):
            return rv

        return self.asset_root.resolve_url_path(pieces)

    @property
    def root(self):
        """The root page of the database."""
        return self.get('/', persist=True)

    @property
    def asset_root(self):
        """The root of the asset tree."""
        return Directory(self, name='',
                         path=os.path.join(self.db.env.root_path, 'assets'))

    def get(self, path, persist=True):
        """Loads a record by path."""
        rv = self.cache['record', path]
        if rv is not None:
            return rv

        raw_data = self.db.load_raw_data(path)
        if raw_data is None:
            return

        datamodel = self.db.get_datamodel_for_raw_data(raw_data, self)
        cls = self.db.get_record_class(datamodel, raw_data)
        rv = cls(self, datamodel.process_raw_data(raw_data, self))
        self.db.postprocess_record(rv, persist)
        return self.db.track_record_dependency(rv)

    def query(self, path=None):
        """Queries the database either at root level or below a certain
        path.  This is the recommended way to interact with toplevel data.
        The alternative is to work with the :attr:`root` document.
        """
        return Query(path='/' + (path or '').strip('/'), pad=self)


class RecordCache(object):

    def __init__(self, ephemeral_cache_size=500):
        self.persistent = {}
        self.ephemeral = LRUCache(ephemeral_cache_size)

    def is_persistent(self, record):
        cache_key = record.record_classification, record['_path']
        return cache_key in self.persistent

    def remember(self, record):
        cache_key = record.record_classification, record['_path']
        if cache_key in self.persistent or cache_key in self.ephemeral:
            return
        self.ephemeral[cache_key] = record

    def persist(self, record):
        cache_key = record.record_classification, record['_path']
        self.persistent[cache_key] = record
        try:
            del self.ephemeral[cache_key]
        except KeyError:
            pass

    def persist_if_cached(self, record):
        cache_key = record.record_classification, record['_path']
        if cache_key in self.ephemeral:
            self.persist(record)

    def __getitem__(self, key):
        rv = self.persistent.get(key)
        if rv is not None:
            return rv
        rv = self.ephemeral.get(key)
        if rv is not None:
            return rv
