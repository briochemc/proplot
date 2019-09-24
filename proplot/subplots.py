#!/usr/bin/env python3
"""
The starting point for creating custom ProPlot figures and axes.
The `subplots` function is all you'll need to directly use here.
It returns a `Figure` instance and an `axes_grid` container of
`~proplot.axes.Axes` axes, whose positions are controlled by the
`FlexibleGridSpec` class.

.. raw:: html

   <h1>Developer notes</h1>

Matplotlib permits arbitrarily many gridspecs per figure, and
encourages serial calls to `~matplotlib.figure.Figure.add_subplot`. By
contrast, ProPlot permits only *one* `~matplotlib.gridspec.GridSpec` per
figure, and forces the user to construct axes and figures with `subplots`. On
the whole, matplotlib's approach is fairly cumbersome, but necessary owing to
certain design limitations. The following describes how ProPlot addresses
these limitations.

* Matplotlib's `~matplotlib.gridspec.GridSpec` can only implement *uniform*
  spacing between rows and columns of subplots. This seems to be the main
  reason the `~matplotlib.gridspec.GridSpecFromSubplotSpec` class was
  invented. To get variable spacing, users have to build their own nested
  `~matplotlib.gridspec.GridSpec` objects and manually pass
  `~matplotlib.gridspec.SubplotSpec` objects to
  `~matplotlib.figure.Figure.add_subplot`.

  ProPlot's `FlexibleGridSpec` class permits *variable* spacing between
  rows and columns of subplots. This largely eliminates the need for
  `~matplotlib.gridspec.GridSpecFromSubplotSpec`, and prevents users
  from having to pass their own `~matplotlib.gridspec.SubplotSpec` objects
  to `~matplotlib.figure.Figure.add_subplot`.

* Matplotlib's `~matplotlib.pyplot.subplots` can only draw simple 2D
  arrangements of subplots. To make complex arrangements, the user must
  generate their own `~matplotlib.gridspec.SubplotSpec` instances, or serially
  pass 3-digit integers or 3 positional arguments
  `~matplotlib.figure.Figure.add_subplot`. And to simplify this workflow, the
  gridspec geometry must be allowed to vary. For example, to draw a simple
  grid with two subplots on the top and one subplot on the bottom, the user
  can serially pass ``221``, ``222``, and ``212`` to
  `~matplotlib.figure.Figure.add_subplot`. But if the gridspec geometry
  was required to remain the same, instead of ``212``, the user would have to
  use ``2, 2, (3,4)``, which is much more cumbersome. So, the figure
  must support multiple gridspec geometries -- i.e. it must support multiple
  gridspecs!

  With ProPlot, users can build complex subplot grids using the ``array``
  `subplots` argument. This means that serial calls to
  `~matplotlib.figure.Figure.add_subplot` are no longer necessary, and we can
  use just *one* gridspec for the whole figure without imposing any new
  limitations. Among other things, this simplifies the "tight layout"
  algorithm.


Also note that ProPlot's `subplots` returns an `axes_grid` of axes, while
matplotlib's `~matplotlib.pyplot.subplots` returns a 2D `~numpy.ndarray`,
a 1D `~numpy.ndarray`, or the axes itself. `axes_grid` was invented in
part because `subplots` can draw arbitrarily complex arrangements of
subplots, not just simple grids. `axes_grid` is a `list` subclass supporting
1D indexing (e.g. ``axs[0]``), but permits 2D indexing (e.g. ``axs[1,0]``)
*just in case* the user *happened* to draw a clean 2D matrix of subplots.
The `~axes_grid.__getattr__` override also means it no longer matters
whether you are calling a method on an axes or a singleton `axes_grid` of axes.
"""
# NOTE: Importing backend causes issues with sphinx, and anyway not sure it's
# always included, so make it optional
import os
import numpy as np
import functools
import warnings
import matplotlib.pyplot as plt
import matplotlib.figure as mfigure
import matplotlib.transforms as mtransforms
import matplotlib.gridspec as mgridspec
try:
    import matplotlib.backends.backend_macosx as mbackend
except ImportError:
    mbackend = None
from .rctools import rc
from .utils import _notNone, _counter, units
from . import projs, axes
__all__ = [
    'axes_grid', 'close', 'show', 'subplots', 'Figure', 'FlexibleGridSpec',
    ]

# Translation
SIDE_TRANSLATE = {
    'l':'left',
    'r':'right',
    'b':'bottom',
    't':'top',
    }

# Dimensions of figures for common journals
JOURNAL_SPECS = {
    'pnas1': '8.7cm',
    'pnas2': '11.4cm',
    'pnas3': '17.8cm',
    'ams1': 3.2, # spec is in inches
    'ams2': 4.5,
    'ams3': 5.5,
    'ams4': 6.5,
    'agu1': ('95mm',  '115mm'),
    'agu2': ('190mm', '115mm'),
    'agu3': ('95mm',  '230mm'),
    'agu4': ('190mm', '230mm'),
    'aaas1': '5.5cm', # AAAS (e.g., Science) 1 column
    'aaas2': '12cm', # AAAS 2 column
    }


#-----------------------------------------------------------------------------#
# Miscellaneous stuff
#-----------------------------------------------------------------------------#
# Wrapper functions, so user doesn't have to import pyplot
def close():
    """Alias for ``matplotlib.pyplot.close('all')``, included so you don't have
    to import `~matplotlib.pyplot`. Closes all figures stored
    in memory."""
    plt.close('all') # easy peasy

def show():
    """Alias for ``matplotlib.pyplot.show()``, included so you don't have
    to import `~matplotlib.pyplot`. Note this command should be
    unnecessary if you are doing inline iPython notebook plotting and ran the
    `~proplot.notebook.nbsetup` command."""
    plt.show()

# Helper classes
class axes_grid(list):
    """List subclass and pseudo-2D array that is used as a container for the
    list of axes returned by `subplots`, lists of figure panels, and lists of
    stacked axes panels. The shape of the array is stored in the ``shape``
    attribute. See the `~axes_grid.__getattr__` and `~axes_grid.__getitem__`
    methods for details."""
    def __init__(self, objs, n=1, order='C'):
        """
        Parameters
        ----------
        objs : list-like
            1D iterable of `~proplot.axes.Axes` instances.
        n : int, optional
            The length of the fastest-moving dimension, i.e. the number of
            columns when `order` is ``'C'``, and the number of rows when `order`
            is ``'F'``. Used to treat lists as pseudo-2D arrays.
        order : {'C', 'F'}, optional
            Whether 1D indexing returns results in row-major (C-style) or
            column-major (Fortran-style) order, respectively. Used to treat
            lists as pseudo-2D arrays.
        """
        if not all(isinstance(obj, axes.Axes) for obj in objs):
            raise ValueError(f'Axes grid must be filled with Axes instances, got {objs!r}.')
        self._n = n
        self._order = order
        super().__init__(objs)
        self.shape = (len(self)//n, n)[::(1 if order == 'C' else -1)]

    def __repr__(self):
        return 'axes_grid([' + ', '.join(str(ax) for ax in self) + '])'

    def __setitem__(self, key, value):
        """Pseudo immutability, raises error."""
        raise LookupError('axes_grid is immutable.')

    def __getitem__(self, key):
        """If an integer is passed, the item is returned, and if a slice is passed,
        an `axes_grid` of the items is returned. You can also use 2D indexing,
        and the corresponding axes in the axes grid will be chosen.

        Example
        -------

        >>> import proplot as plot
        ... f, axs = plot.subplots(nrows=3, ncols=3, colorbars='b', bstack=2)
        ... axs[0] # the subplot in the top-right corner
        ... axs[3] # the first subplot in the second row
        ... axs[1,2] # the subplot in the second row, third from the left
        ... axs[:,0] # the subplots in the first column

        """
        # Allow 2D specification
        if isinstance(key, tuple) and len(key) == 1:
            key = key[0]
        if not isinstance(key, tuple): # do not expand single slice to list of integers or we get recursion! len() operator uses __getitem__!
            axlist = isinstance(key, slice)
            objs = list.__getitem__(self, key)
        elif len(key) == 2:
            axlist = any(isinstance(ikey, slice) for ikey in key)
            # Expand keys
            keys = []
            order = self._order
            for i,ikey in enumerate(key):
                if (i == 1 and order == 'C') or (i == 0 and order != 'C'):
                    n = self._n
                else:
                    n = len(self)//self._n
                if isinstance(ikey, slice):
                    start, stop, step = ikey.start, ikey.stop, ikey.step
                    if start is None:
                        start = 0
                    elif start < 0:
                        start = n + start
                    if stop is None:
                        stop = n
                    elif stop < 0:
                        stop = n + stop
                    if step is None:
                        step = 1
                    ikeys = [*range(start, stop, step)]
                else:
                    if ikey < 0:
                        ikey = n + ikey
                    ikeys = [ikey]
                keys.append(ikeys)
            # Get index pairs and get objects
            # Note that in double for loop, right loop varies fastest, so
            # e.g. axs[:,:] delvers (0,0), (0,1), ..., (0,N), (1,0), ...
            # Remember for order == 'F', axes_grid was sent a list unfurled in
            # column-major order, so we replicate row-major indexing syntax by
            # reversing the order of the keys.
            objs = []
            if self._order == 'C':
                idxs = [key0*self._n + key1 for key0 in keys[0] for key1 in keys[1]]
            else:
                idxs = [key1*self._n + key0 for key1 in keys[1] for key0 in keys[0]]
            for idx in idxs:
                objs.append(list.__getitem__(self, idx))
            if not axlist: # objs will always be length 1
                objs = objs[0]
        else:
            raise IndexError

        # Return
        if axlist:
            return axes_grid(objs)
        else:
            return objs

    def __getattr__(self, attr):
        """
        If the attribute is *callable*, returns a dummy function that loops
        through each identically named method, calls them in succession, and
        returns a tuple of the results. This lets you call arbitrary methods
        on multiple axes at once! If the `axes_grid` has length ``1``,
        just returns the single result. If the attribute is *not callable*,
        returns a tuple of identically named attributes for every object in
        the list.

        Example
        -------

        >>> import proplot as plot
        ... f, axs = plot.subplots(nrows=2, ncols=2)
        ... axs.format(...) # calls "format" on all subplots in the list
        ... paxs = axs.panel_axes('r')
        ... paxs.format(...) # calls "format" on all panels in the axes_grid returned by "axs.panel_axes"

        """
        if not self:
            raise AttributeError(f'Invalid attribute {attr!r}, axes grid {self!r} is empty.')
        objs = (*(getattr(ax, attr) for ax in self),) # may raise error

        # Objects
        if not any(callable(_) for _ in objs):
            if len(self) == 1:
                return objs[0]
            else:
                return objs
        # Methods
        # NOTE: Must manually copy docstring because help() cannot inherit it
        elif all(callable(_) for _ in objs):
            @functools.wraps(objs[0])
            def _iterator(*args, **kwargs):
                ret = []
                for func in objs:
                    ret.append(func(*args, **kwargs))
                ret = (*ret,)
                if len(self) == 1:
                    return ret[0]
                elif all(res is None for res in ret):
                    return None
                elif all(isinstance(res, axes.Axes) for res in ret):
                    return axes_grid(ret, n=self._n, order=self._order)
                else:
                    return ret
            try:
                orig = getattr(super(axes.Axes, self[0]), attr)
                _iterator.__doc__ = orig.__doc__
            except AttributeError:
                pass
            return _iterator

        # Mixed
        raise AttributeError(f'Found mixed types for attribute {attr!r}.')

    # TODO: No more putting panels, legends, colorbars on the SubplotSpec.
    # Put them in the margin, increase default space, and lock them to
    # subplot bounds with locators, borrowing from axes_grid1 toolkit
    # TODO: Consider adding Github issue, would be major API change.
    # def colorbar(self, loc=None):
    #     """Draws a colorbar that spans axes in the selected range."""
    #     for ax in self:
    #         pass
    #
    # def legend(self, loc=None):
    #     """Draws a legend that spans axes in the selected range."""
    #     for ax in self:
    #         pass
    #
    # def text(self, loc=None):
    #     """Draws text that spans axes in the selected range."""
    #     for ax in self:
    #         pass

#-----------------------------------------------------------------------------#
# Gridspec classes
#-----------------------------------------------------------------------------#
class FlexibleSubplotSpec(mgridspec.SubplotSpec):
    """
    Adds two helper methods to `~matplotlib.gridspec.SubplotSpec` that return
    the geometry *excluding* rows and columns allocated for spaces.
    """
    def get_active_geometry(self):
        """Returns the number of rows, number of columns, and 1D subplot
        location indices, ignoring rows and columns allocated for spaces."""
        nrows, ncols, row1, row2, col1, col2 = self.get_active_rows_columns()
        num1 = row1*ncols + col1
        num2 = row2*ncols + col2
        return nrows, ncols, num1, num2

    def get_active_rows_columns(self):
        """Returns the number of rows, number of columns, first subplot row,
        last subplot row, first subplot column, and last subplot column,
        ignoring rows and columns allocated for spaces."""
        gridspec = self.get_gridspec()
        nrows, ncols = gridspec.get_geometry()
        row1, col1 = divmod(self.num1, ncols)
        if self.num2 is not None:
            row2, col2 = divmod(self.num2, ncols)
        else:
            row2 = row1
            col2 = col1
        return nrows//2, ncols//2, row1//2, row2//2, col1//2, col2//2

class FlexibleGridSpec(mgridspec.GridSpec):
    """
    `~matplotlib.gridspec.GridSpec` generalization that allows for grids with
    *variable spacing* between successive rows and columns of axes.

    Accomplishes this by actually drawing ``nrows*2 + 1`` and ``ncols*2 + 1``
    `~matplotlib.gridspec.GridSpec` rows and columns, setting `wspace`
    and `hspace` to ``0``, and masking out every other row and column
    of the `~matplotlib.gridspec.GridSpec`, so they act as "spaces".
    These "spaces" are then allowed to vary in width using the builtin
    `width_ratios` and `height_ratios` properties.
    """
    def __init__(self, figure, nrows=1, ncols=1, **kwargs):
        """
        Parameters
        ----------
        figure : `Figure`
            The figure instance filled by this gridspec. Unlike
            `~matplotlib.gridspec.GridSpec`, this argument is required.
        nrows, ncols : int, optional
            The number of rows and columns on the subplot grid.
        hspace, wspace : float or list of float
            The vertical and horizontal spacing between rows and columns of
            subplots, respectively. In `~proplot.subplots.subplots`, ``wspace``
            and ``hspace`` are in physical units. When calling
            `FlexibleGridSpec` directly, values are scaled relative to
            the average subplot height or width.

            If float, the spacing is identical between all rows and columns. If
            list of float, the length of the lists must equal ``nrows-1``
            and ``ncols-1``, respectively.
        height_ratios, width_ratios : list of float
            Ratios for the relative heights and widths for rows and columns
            of subplots, respectively. For example, ``width_ratios=(1,2)``
            scales a 2-column gridspec so that the second column is twice as
            wide as the first column.
        left, right, top, bottom : float or str
            Passed to `~matplotlib.gridspec.GridSpec`, denotes the margin
            positions in figure-relative coordinates.
        **kwargs
            Passed to `~matplotlib.gridspec.GridSpec`.
        """
        self._nrows = nrows*2-1 # used with get_geometry
        self._ncols = ncols*2-1
        self._nrows_active = nrows
        self._ncols_active = ncols
        wratios, hratios, kwargs = self._spaces_as_ratios(**kwargs)
        super().__init__(self._nrows, self._ncols,
                hspace=0, wspace=0, # we implement these as inactive rows/columns
                width_ratios=wratios,
                height_ratios=hratios,
                figure=figure,
                **kwargs,
                )

    def __getitem__(self, key):
        """Magic obfuscation that renders `~matplotlib.gridspec.GridSpec`
        rows and columns designated as 'spaces' inaccessible."""
        nrows, ncols = self.get_geometry()
        nrows_active, ncols_active = self.get_active_geometry()
        if not isinstance(key, tuple): # usage gridspec[1,2]
            num1, num2 = self._normalize(key, nrows_active * ncols_active)
        else:
            if len(key) == 2:
                k1, k2 = key
            else:
                raise ValueError(f'Invalid index {key!r}.')
            num1 = self._normalize(k1, nrows_active)
            num2 = self._normalize(k2, ncols_active)
            num1, num2 = np.ravel_multi_index((num1, num2), (nrows, ncols))
        num1 = self._positem(num1)
        num2 = self._positem(num2)
        return FlexibleSubplotSpec(self, num1, num2)

    @staticmethod
    def _positem(size):
        """Account for negative indices."""
        if size < 0:
            return 2*(size+1) - 1 # want -1 to stay -1, -2 becomes -3, etc.
        else:
            return size*2

    @staticmethod
    def _normalize(key, size):
        """Transform gridspec index into standardized form."""
        if isinstance(key, slice):
            start, stop, _ = key.indices(size)
            if stop > start:
                return start, stop - 1
        else:
            if key < 0:
                key += size
            if 0 <= key < size:
                return key, key
        raise IndexError(f"Invalid index: {key} with size {size}.")

    def _spaces_as_ratios(self,
        hspace=None, wspace=None, # spacing between axes
        height_ratios=None, width_ratios=None,
        **kwargs):
        """For keyword arg usage, see `FlexibleGridSpec`."""
        # Parse flexible input
        nrows, ncols = self.get_active_geometry()
        hratios = np.atleast_1d(_notNone(height_ratios, 1))
        wratios = np.atleast_1d(_notNone(width_ratios,  1))
        hspace = np.atleast_1d(_notNone(hspace, np.mean(hratios)*0.10)) # this is relative to axes
        wspace = np.atleast_1d(_notNone(wspace, np.mean(wratios)*0.10))
        if len(wspace) == 1:
            wspace = np.repeat(wspace, (ncols-1,)) # note: may be length 0
        if len(hspace) == 1:
            hspace = np.repeat(hspace, (nrows-1,))
        if len(wratios) == 1:
            wratios = np.repeat(wratios, (ncols,))
        if len(hratios) == 1:
            hratios = np.repeat(hratios, (nrows,))

        # Verify input ratios and spacings
        # Translate height/width spacings, implement as extra columns/rows
        if len(hratios) != nrows:
            raise ValueError(f'Got {nrows} rows, but {len(hratios)} hratios.')
        if len(wratios) != ncols:
            raise ValueError(f'Got {ncols} columns, but {len(wratios)} wratios.')
        if len(wspace) != ncols-1:
            raise ValueError(f'Require {ncols-1} width spacings for {ncols} columns, got {len(wspace)}.')
        if len(hspace) != nrows-1:
            raise ValueError(f'Require {nrows-1} height spacings for {nrows} rows, got {len(hspace)}.')

        # Assign spacing as ratios
        nrows, ncols = self.get_geometry()
        wratios_final = [None]*ncols
        wratios_final[::2] = [*wratios]
        if ncols > 1:
            wratios_final[1::2] = [*wspace]
        hratios_final = [None]*nrows
        hratios_final[::2] = [*hratios]
        if nrows > 1:
            hratios_final[1::2] = [*hspace]
        return wratios_final, hratios_final, kwargs # bring extra kwargs back

    def get_margins(self):
        """Returns left, bottom, right, top values. Not sure why this method
        doesn't already exist on `~matplotlib.gridspec.GridSpec`."""
        return self.left, self.bottom, self.right, self.top

    def get_hspace(self):
        """Returns row ratios allocated for spaces."""
        return self.get_height_ratios()[1::2]

    def get_wspace(self):
        """Returns column ratios allocated for spaces."""
        return self.get_width_ratios()[1::2]

    def get_active_height_ratios(self):
        """Returns height ratios excluding slots allocated for spaces."""
        return self.get_height_ratios()[::2]

    def get_active_width_ratios(self):
        """Returns width ratios excluding slots allocated for spaces."""
        return self.get_width_ratios()[::2]

    def get_active_geometry(self):
        """Returns the number of active rows and columns, i.e. the rows and
        columns that aren't skipped by `~FlexibleGridSpec.__getitem__`."""
        return self._nrows_active, self._ncols_active

    def update(self, **kwargs):
        """
        Updates the width ratios, height ratios, gridspec margins, and spacing
        allocated between subplot rows and columns.

        The default `~matplotlib.gridspec.GridSpec.update` tries to update
        positions for axes on all active figures -- but this can fail after
        successive figure edits if it has been removed from the figure
        manager. So, we explicitly require that the gridspec is dedicated to
        a particular `~matplotlib.figure.Figure` instance, and just edit axes
        positions for axes on that instance.
        """
        # Convert spaces to ratios
        wratios, hratios, kwargs = self._spaces_as_ratios(**kwargs)
        self.set_width_ratios(wratios)
        self.set_height_ratios(hratios)

        # Validate args
        kwargs.pop('ncols', None)
        kwargs.pop('nrows', None)
        self.left   = kwargs.pop('left', None)
        self.right  = kwargs.pop('right', None)
        self.bottom = kwargs.pop('bottom', None)
        self.top    = kwargs.pop('top', None)
        if kwargs:
            raise ValueError(f'Unknown keyword arg(s): {kwargs}.')

        # Apply to figure and all axes
        fig = self.figure
        fig.subplotpars.update(self.left, self.bottom, self.right, self.top)
        for ax in fig.axes:
            ax.update_params()
            ax.set_position(ax.figbox)
        fig.stale = True

#-----------------------------------------------------------------------------#
# Helper funcs
#-----------------------------------------------------------------------------#
def _panels_kwargs(side,
    share=None, width=None, space=None,
    filled=False, figure=False):
    """Converts global keywords like `space` and `width` to side-local
    keywords like `lspace` and `lwidth`, and applies default settings."""
    # Return values
    # NOTE: Make default legend width same as default colorbar width, in
    # case user draws legend and colorbar panel in same row or column!
    s = side[0]
    if s not in 'lrbt':
        raise ValueError(f'Invalid panel spec {side!r}.')
    space_orig = units(space)
    if filled:
        default = rc['colorbar.width']
    else:
        default = rc['subplots.panelwidth']
    share = _notNone(share, (not filled))
    width = units(_notNone(width, default))
    space = _notNone(units(space), units(rc['subplots.' + ('panel' if share
        and not figure
        else 'xlab' if s == 'b' else 'ylab' if s == 'l'
        else 'inner' if figure else 'panel') + 'space']))
    return share, width, space, space_orig

def _subplots_geometry(**kwargs):
    """Saves arguments passed to `subplots`, calculates gridspec settings and
    figure size necessary for requested geometry, and returns keyword args
    necessary to reconstruct and modify this configuration. Note that
    `wspace`, `hspace`, `left`, `right`, `top`, and `bottom` always have fixed
    physical units, then we scale figure width, figure height, and width
    and height ratios to accommodate spaces."""
    # Dimensions and geometry
    nrows, ncols       = kwargs['nrows'], kwargs['ncols']
    aspect, xref, yref = kwargs['aspect'], kwargs['xref'], kwargs['yref']
    width, height      = kwargs['width'], kwargs['height']
    axwidth, axheight  = kwargs['axwidth'], kwargs['axheight']
    # Gridspec settings
    wspace, hspace   = kwargs['wspace'], kwargs['hspace']
    wratios, hratios = kwargs['wratios'], kwargs['hratios']
    left, bottom     = kwargs['left'], kwargs['bottom']
    right, top       = kwargs['right'], kwargs['top']
    # Panel string toggles, lists containing empty strings '' (indicating a
    # main axes), or one of 'l', 'r', 'b', 't' (indicating axes panels) or
    # 'f' (indicating figure panels)
    wpanels, hpanels = kwargs['wpanels'], kwargs['hpanels']

    # Checks, important now that we modify gridspec geometry
    if len(hratios) != nrows:
        raise ValueError(f'Expected {nrows} width ratios for {nrows} rows, got {len(hratios)}.')
    if len(wratios) != ncols:
        raise ValueError(f'Expected {ncols} width ratios for {ncols} columns, got {len(wratios)}.')
    if len(hspace) != nrows - 1:
        raise ValueError(f'Expected {nrows - 1} hspaces for {nrows} rows, got {len(hspace)}.')
    if len(wspace) != ncols - 1:
        raise ValueError(f'Expected {ncols - 1} wspaces for {ncols} columns, got {len(wspace)}.')
    if len(hpanels) != nrows:
        raise ValueError(f'Expected {nrows} hpanel toggles for {nrows} rows, got {len(hpanels)}.')
    if len(wpanels) != ncols:
        raise ValueError(f'Expected {ncols} wpanel toggles for {ncols} columns, got {len(wpanels)}.')

    # Get indices corresponding to main axes or main axes space slots
    idxs_ratios, idxs_space = [], []
    for panels in (hpanels,wpanels):
        # Ratio indices
        mask = np.array([bool(s) for s in panels])
        ratio_idxs, = np.where(~mask)
        idxs_ratios.append(ratio_idxs)
        # Space indices
        space_idxs = []
        for idx in ratio_idxs[:-1]: # exclude last axes slot
            offset = 1
            while panels[idx + offset] not in 'rbf': # main space is next to this
                offset += 1
            space_idxs.append(idx + offset - 1)
        idxs_space.append(space_idxs)
    # Separate the panel and axes ratios
    hratios_main = [hratios[idx] for idx in idxs_ratios[0]]
    wratios_main = [wratios[idx] for idx in idxs_ratios[1]]
    hratios_panels = [ratio for idx,ratio in enumerate(hratios) if idx not in idxs_ratios[0]]
    wratios_panels = [ratio for idx,ratio in enumerate(wratios) if idx not in idxs_ratios[1]]
    hspace_main = [hspace[idx] for idx in idxs_space[0]]
    wspace_main = [wspace[idx] for idx in idxs_space[1]]
    # Reduced geometry
    nrows_main = len(hratios_main)
    ncols_main = len(wratios_main)

    # Get reference properties, account for panel slots in space and ratios
    # TODO: Shouldn't panel space be included in these calculations?
    (x1, x2), (y1, y2) = xref, yref
    dx, dy = x2 - x1 + 1, y2 - y1 + 1
    rwspace = sum(wspace_main[x1:x2])
    rhspace = sum(hspace_main[y1:y2])
    rwratio = (ncols_main*sum(wratios_main[x1:x2+1]))/(dx*sum(wratios_main))
    rhratio = (nrows_main*sum(hratios_main[y1:y2+1]))/(dy*sum(hratios_main))
    if rwratio == 0 or rhratio == 0:
        raise RuntimeError(f'Something went wrong, got wratio={rwratio!r} and hratio={rhratio!r} for reference axes.')
    if np.iterable(aspect):
        aspect = aspect[0]/aspect[1]

    # Determine figure and axes dims from input in width or height dimenion.
    # For e.g. common use case [[1,1,2,2],[0,3,3,0]], make sure we still scale
    # the reference axes like square even though takes two columns of gridspec!
    auto_width = (width is None and height is not None)
    auto_height = (height is None and width is not None)
    if width is None and height is None: # get stuff directly from axes
        if axwidth is None and axheight is None:
            axwidth = units(rc['subplots.axwidth'])
        if axheight is not None:
            auto_width = True
            axheight_all = (nrows_main*(axheight - rhspace))/(dy*rhratio)
            height = axheight_all + top + bottom + sum(hspace) + sum(hratios_panels)
        if axwidth is not None:
            auto_height = True
            axwidth_all = (ncols_main*(axwidth - rwspace))/(dx*rwratio)
            width = axwidth_all + left + right + sum(wspace) + sum(wratios_panels)
        if axwidth is not None and axheight is not None:
            auto_width = auto_height = False
    else:
        if height is not None:
            axheight_all = height - top - bottom - sum(hspace) - sum(hratios_panels)
            axheight = (axheight_all*dy*rhratio)/nrows_main + rhspace
        if width is not None:
            axwidth_all = width - left - right - sum(wspace) - sum(wratios_panels)
            axwidth = (axwidth_all*dx*rwratio)/ncols_main + rwspace

    # Automatically figure dim that was not specified above
    if auto_height:
        axheight = axwidth/aspect
        axheight_all = (nrows_main*(axheight - rhspace))/(dy*rhratio)
        height = axheight_all + top + bottom + sum(hspace) + sum(hratios_panels)
    elif auto_width:
        axwidth = axheight*aspect
        axwidth_all = (ncols_main*(axwidth - rwspace))/(dx*rwratio)
        width = axwidth_all + left + right + sum(wspace) + sum(wratios_panels)
    if axwidth_all < 0:
        raise ValueError(f"Not enough room for axes (would have width {axwidth_all}). Try using tight=False, increasing figure width, or decreasing 'left', 'right', or 'wspace' spaces.")
    if axheight_all < 0:
        raise ValueError(f"Not enough room for axes (would have height {axheight_all}). Try using tight=False, increasing figure height, or decreasing 'top', 'bottom', or 'hspace' spaces.")

    # Reconstruct the ratios array with physical units for subplot slots
    # The panel slots are unchanged because panels have fixed widths
    wratios_main = axwidth_all*np.array(wratios_main)/sum(wratios_main)
    hratios_main = axheight_all*np.array(hratios_main)/sum(hratios_main)
    for idx,ratio in zip(idxs_ratios[0],hratios_main):
        hratios[idx] = ratio
    for idx,ratio in zip(idxs_ratios[1],wratios_main):
        wratios[idx] = ratio

    # Convert margins to figure-relative coordinates
    left = left/width
    bottom = bottom/height
    right = 1 - right/width
    top = 1 - top/height

    # Return gridspec keyword args
    gridspec_kw = {
        'ncols': ncols, 'nrows': nrows,
        'wspace': wspace, 'hspace': hspace,
        'width_ratios': wratios, 'height_ratios': hratios,
        'left': left, 'bottom': bottom, 'right': right, 'top': top,
        }

    return (width, height), gridspec_kw, kwargs

#-----------------------------------------------------------------------------#
# Figure class and helper classes
#-----------------------------------------------------------------------------#
class _unlocker(object):
    """Suppresses warning message when adding subplots, and cleanly resets
    lock setting if exception raised."""
    def __init__(self, fig):
        self._fig = fig
    def __enter__(self):
        self._fig._locked = False
    def __exit__(self, *args):
        self._fig._locked = True

class _hidelabels(object):
    """Hides objects temporarily so they are ignored by the tight bounding box
    algorithm."""
    def __init__(self, *args):
        self._labels = args
    def __enter__(self):
        for label in self._labels:
            label.set_visible(False)
    def __exit__(self, *args):
        for label in self._labels:
            label.set_visible(True)

class Figure(mfigure.Figure):
    """The `~matplotlib.figure.Figure` class returned by `subplots`. At
    draw-time, an improved tight layout algorithm is employed, and
    the space around the figure edge, between subplots, and between
    panels is changed to accommodate subplot content. Figure dimensions
    may be automatically scaled to preserve subplot aspect ratios."""
    def __init__(self,
        tight=None,
        pad=None, axpad=None, panelpad=None, includepanels=False,
        autoformat=True,
        ref=1, order='C', # documented in subplots but needed here
        subplots_kw=None, gridspec_kw=None, subplots_orig_kw=None,
        tight_layout=None, constrained_layout=None,
        **kwargs):
        """
        Parameters
        ----------
        tight : bool, optional
            Toggles automatic tight layout adjustments. Default is
            :rc:`tight`.
        pad : float or str, optional
            Padding around edge of figure. Units are interpreted by
            `~proplot.utils.units`. Default is :rc:`subplots.pad`.
        axpad : float or str, optional
            Padding between subplots in adjacent columns and rows. Units are
            interpreted by `~proplot.utils.units`. Default is
            :rc:`subplots.axpad`.
        panelpad : float or str, optional
            Padding between subplots and axes panels, and between "stacked"
            panels. Units are interpreted by `~proplot.utils.units`.Default is
            :rc:`subplots.panelpad`.
        includepanels : bool, optional
            Whether to include panels when centering *x* axis labels,
            *y* axis labels, and figure "super titles" along the edge of the
            subplot grid. Default is ``False``.
        autoformat : bool, optional
            Whether to automatically configure *x* axis labels, *y* axis
            labels, axis formatters, axes titles, colorbar labels, and legend
            labels when a `~pandas.Series`, `~pandas.DataFrame` or
            `~xarray.DataArray` with relevant metadata is passed to a plotting
            command.
        gridspec_kw, subplots_kw, subplots_orig_kw
            Keywords used for initializing the main gridspec, for initializing
            the figure, and original spacing keyword args used for initializing
            the figure that override tight layout spacing.

        Other parameters
        ----------------
        ref, order
            Documented in `subplots`.
        tight_layout, constrained_layout
            Ignored, because ProPlot uses its own tight layout algorithm.
        **kwargs
            Passed to `matplotlib.figure.Figure`.
        """
        # Initialize first, because need to provide fully initialized figure
        # as argument to gridspec, because matplotlib tight_layout does that
        if tight_layout or constrained_layout:
            warnings.warn(f'Ignoring tight_layout={tight_layout} and contrained_layout={constrained_layout}. ProPlot uses its own tight layout algorithm, activated by default or with tight=True.')
        super().__init__(**kwargs)
        self._locked = False
        self._pad = units(_notNone(pad, rc['subplots.pad']))
        self._axpad = units(_notNone(axpad, rc['subplots.axpad']))
        self._panelpad = units(_notNone(panelpad, rc['subplots.panelpad']))
        self._auto_format = autoformat
        self._auto_tight_layout = _notNone(tight, rc['tight'])
        self._include_panels = includepanels
        self._order = order # used for configuring panel axes_grids
        self._ref_num = ref
        self._axes_main = []
        self._subplots_orig_kw = subplots_orig_kw
        self._subplots_kw = subplots_kw
        self._bpanels = []
        self._tpanels = []
        self._lpanels = []
        self._rpanels = []
        gridspec = FlexibleGridSpec(self, **(gridspec_kw or {}))
        nrows, ncols = gridspec.get_active_geometry()
        self._barray = np.empty((0, ncols), dtype=bool)
        self._tarray = np.empty((0, ncols), dtype=bool)
        self._larray = np.empty((0, nrows), dtype=bool)
        self._rarray = np.empty((0, nrows), dtype=bool)
        self._gridspec_main = gridspec
        self.suptitle('') # add _suptitle attribute

    @_counter
    def _add_axes_panel(self, ax, side, filled=False, **kwargs):
        """Hidden method that powers `~proplot.axes.panel_axes`."""
        # Interpret args
        # NOTE: Axis sharing not implemented for figure panels, 99% of the
        # time this is just used as construct for adding global colorbars and
        # legends, really not worth implementing axis sharing
        s = side[0]
        if s not in 'lrbt':
            raise ValueError(f'Invalid side {side!r}.')
        ax = ax._panel_parent or ax # redirect to main axes
        side = SIDE_TRANSLATE[s]
        share, width, space, space_orig = _panels_kwargs(s,
                filled=filled, figure=False, **kwargs)

        # Get gridspec and subplotspec indices
        subplotspec = ax.get_subplotspec()
        nrows, ncols, row1, row2, col1, col2 = subplotspec.get_active_rows_columns()
        pgrid = getattr(ax, '_' + s + 'panels')
        offset = (len(pgrid)*bool(pgrid)) + 1
        if s in 'lr':
            iratio = (col1 - offset if s == 'l' else col2 + offset)
            idx1 = slice(row1, row2 + 1)
            idx2 = iratio
        else:
            iratio = (row1 - offset if s == 't' else row2 + offset)
            idx1 = iratio
            idx2 = slice(col1, col2 + 1)
        gridspec_prev = self._gridspec_main
        gridspec = self._insert_row_column(side, iratio,
            width, space, space_orig, figure=False,
            )
        if gridspec is not gridspec_prev:
            if s == 't':
                idx1 += 1
            elif s == 'l':
                idx2 += 1

        # Draw and setup panel
        with self._unlock():
            pax = self.add_subplot(gridspec[idx1,idx2],
                    sharex=ax._sharex_level, sharey=ax._sharey_level,
                    projection='cartesian')
        getattr(ax, '_' + s + 'panels').append(pax)
        pax._panel_side = side
        pax._panel_share = share
        pax._panel_parent = ax

        # Axis sharing and axis setup only for non-legend or colorbar axes
        if not filled:
            ax._share_setup()
            axis = (pax.yaxis if side in ('left','right') else pax.xaxis)
            getattr(axis, 'tick_' + side)() # sets tick and tick label positions intelligently
            axis.set_label_position(side)

        return pax

    def _add_figure_panel(self, side,
        span=None, row=None, col=None, rows=None, cols=None,
        **kwargs):
        """Adds figure panels. Also modifies the panel attribute stored
        on the figure to include these panels."""
        # Interpret args and enforce sensible keyword args
        s = side[0]
        if s not in 'lrbt':
            raise ValueError(f'Invalid side {side!r}.')
        side = SIDE_TRANSLATE[s]
        _, width, space, space_orig = _panels_kwargs(s,
                filled=True, figure=True, **kwargs)
        if s in 'lr':
            for key,value in (('col',col),('cols',cols)):
                if value is not None:
                    raise ValueError(f'Invalid keyword arg {key!r} for figure panel on side {side!r}.')
            span = _notNone(span, row, rows, None, names=('span', 'row', 'rows'))
        else:
            for key,value in (('row',row),('rows',rows)):
                if value is not None:
                    raise ValueError(f'Invalid keyword arg {key!r} for figure panel on side {side!r}.')
            span = _notNone(span, col, cols, None, names=('span', 'col', 'cols'))

        # Get props
        subplots_kw = self._subplots_kw
        if s in 'lr':
            panels, nacross = subplots_kw['hpanels'], subplots_kw['ncols']
        else:
            panels, nacross = subplots_kw['wpanels'], subplots_kw['nrows']
        array = getattr(self, '_' + s + 'array')
        npanels, nalong = array.shape

        # Check span array
        span = _notNone(span, (1, nalong))
        if not np.iterable(span) or len(span)==1:
            span = 2*np.atleast_1d(span).tolist()
        if len(span) != 2:
            raise ValueError(f'Invalid span {span!r}.')
        if span[0] < 1 or span[1] > nalong:
            raise ValueError(f'Invalid coordinates in span={span!r}. Coordinates must satisfy 1 <= c <= {nalong}.')
        start, stop = span[0] - 1, span[1] # zero-indexed

        # See if there is room for panel in current figure panels
        # The 'array' is an array of boolean values, where each row corresponds
        # to another figure panel, moving toward the outside, and boolean
        # True indicates the slot has been filled
        iratio = (-1 if s in 'lt' else nacross) # default vals
        for i in range(npanels):
            if not any(array[i,start:stop]):
                array[i,start:stop] = True
                if s in 'lt': # descending array moves us closer to 0
                    # npanels=1, i=0 --> iratio=0
                    # npanels=2, i=0 --> iratio=1
                    # npanels=2, i=1 --> iratio=0
                    iratio = npanels - 1 - i
                else: # descending array moves us closer to nacross-1
                    # npanels=1, i=0 --> iratio=nacross-1
                    # npanels=2, i=0 --> iratio=nacross-2
                    # npanels=2, i=1 --> iratio=nacross-1
                    iratio = nacross - (npanels - i)
                break
        if iratio in (-1, nacross): # add to array
            iarray = np.zeros((1, nalong), dtype=bool)
            iarray[0,start:stop] = True
            array = np.concatenate((array, iarray), axis=0)
            setattr(self, '_' + s + 'array', array)

        # Get gridspec and subplotspec indices
        idxs, = np.where(np.array(panels) == '')
        if len(idxs) != nalong:
            raise RuntimeError('Wut?')
        if s in 'lr':
            idx1 = slice(idxs[start], idxs[stop-1] + 1)
            idx2 = max(iratio, 0)
        else:
            idx1 = max(iratio, 0)
            idx2 = slice(idxs[start], idxs[stop-1] + 1)
        gridspec = self._insert_row_column(side, iratio,
            width, space, space_orig, figure=True,
            )

        # Draw and setup panel
        with self._unlock():
            pax = self.add_subplot(gridspec[idx1,idx2], projection='cartesian')
        getattr(self, '_' + s + 'panels').append(pax)
        pax._panel_side = side
        pax._panel_share = False
        pax._panel_parent = None
        return pax

    def _adjust_aspect(self):
        """Adjust average aspect ratio used for gridspec calculations. This
        fixes grids with identically fixed aspect ratios, e.g. identically
        zoomed-in cartopy projections and imshow images."""
        # Get aspect ratio
        if not self._axes_main:
            return
        ax = self._axes_main[self._ref_num-1]
        mode = ax.get_aspect()
        aspect = None
        if mode == 'equal':
            xscale, yscale = ax.get_xscale(), ax.get_yscale()
            if xscale == 'linear' and yscale == 'linear':
                aspect = 1.0/ax.get_data_ratio()
            elif xscale == 'log' and yscale == 'log':
                aspect = 1.0/ax.get_data_ratio_log()
            else:
                pass # matplotlib issues warning, forces aspect == 'auto'
        # Apply aspect
        # Account for floating point errors
        if aspect is not None:
            aspect = round(aspect*1e10)*1e-10
            subplots_kw = self._subplots_kw
            aspect_prev = round(subplots_kw['aspect']*1e10)*1e-10
            if aspect != aspect_prev:
                subplots_kw['aspect'] = aspect
                figsize, gridspec_kw, _ = _subplots_geometry(**subplots_kw)
                self.set_size_inches(figsize)
                self._gridspec_main.update(**gridspec_kw)

    def _adjust_tight_layout(self, renderer):
        """Applies tight layout scaling that permits flexible figure
        dimensions and preserves panel widths and subplot aspect ratios.
        The `renderer` should be a `~matplotlib.backend_bases.RendererBase`
        instance."""
        # Initial stuff
        axs = self._iter_axes()
        obox = self.bbox_inches # original bbox
        bbox = self.get_tightbbox(renderer)
        gridspec = self._gridspec_main
        subplots_kw = self._subplots_kw
        subplots_orig_kw = self._subplots_orig_kw # tight layout overrides
        if not axs or not subplots_kw or not subplots_orig_kw:
            return

        # Tight box *around* figure
        # Get bounds from old bounding box
        pad = self._pad
        left = bbox.xmin
        bottom = bbox.ymin
        right = obox.xmax - bbox.xmax
        top = obox.ymax - bbox.ymax
        # Apply new bounds, permitting user overrides
        # TODO: Account for bounding box NaNs?
        for key,offset in zip(
            ('left','right','top','bottom'),
            (left,right,top,bottom)
            ):
            previous = subplots_orig_kw[key]
            current = subplots_kw[key]
            subplots_kw[key] = _notNone(previous, current - offset + pad)

        # Get arrays storing gridspec spacing args
        axpad = self._axpad
        panelpad = self._panelpad
        nrows, ncols = gridspec.get_active_geometry()
        wspace, hspace = subplots_kw['wspace'], subplots_kw['hspace']
        wspace_orig = subplots_orig_kw['wspace']
        hspace_orig = subplots_orig_kw['hspace']
        # Get new subplot spacings, axes panel spacing, figure panel spacing
        spaces = []
        for (w, x, y, nacross,
            ispace, ispace_orig) in zip('wh', 'xy', 'yx', (nrows,ncols),
            (wspace,hspace), (wspace_orig,hspace_orig),
            ):
            # Determine which rows and columns correspond to panels
            panels = subplots_kw[w + 'panels']
            jspace = [*ispace]
            ralong = np.array([ax._range_gridspec(x) for ax in axs])
            racross = np.array([ax._range_gridspec(y) for ax in axs])
            for i,(space,space_orig) in enumerate(zip(ispace,ispace_orig)):
                # Figure out whether this is a normal space, or a
                # panel stack space/axes panel space
                pad = axpad
                if (panels[i] in ('l','t') and panels[i+1] in ('l','t','')
                    or panels[i] in ('','r','b') and panels[i+1] in ('r','b')
                    or panels[i] == 'f' and panels[i+1] == 'f'):
                    pad = panelpad
                # Find axes that abutt aginst this space on each row
                groups = []
                filt1 = ralong[:,1] == i # i.e. right/bottom edge abutts against this space
                filt2 = ralong[:,0] == i + 1 # i.e. left/top edge abutts against this space
                for j in range(nacross): # e.g. each row
                    # Get indices
                    filt = (racross[:,0] <= j) & (j <= racross[:,1])
                    if sum(filt) < 2: # no interface here
                        continue
                    idx1, = np.where(filt & filt1)
                    idx2, = np.where(filt & filt2)
                    if idx1.size > 1 or idx2.size > 2:
                        warnings.warn('This should never happen.')
                        continue
                        # raise RuntimeError('This should never happen.')
                    elif not idx1.size or not idx2.size:
                        continue
                    idx1, idx2 = idx1[0], idx2[0]
                    # Put these axes into unique groups. Store groups as
                    # (left axes, right axes) or (bottom axes, top axes) pairs.
                    ax1, ax2 = axs[idx1], axs[idx2]
                    if x != 'x':
                        ax1, ax2 = ax2, ax1 # yrange is top-to-bottom, so make this bottom-to-top
                    newgroup = True
                    for (group1,group2) in groups:
                        if ax1 in group1 or ax2 in group2:
                            newgroup = False
                            group1.add(ax1)
                            group2.add(ax2)
                            break
                    if newgroup:
                        groups.append([{ax1}, {ax2}]) # form new group
                # Get spaces
                # Remember layout is lspace, lspaces[0], rspaces[0], wspace, ...
                # so panels spaces are located where i % 3 is 1 or 2
                jspaces = []
                for (group1,group2) in groups:
                    x1 = max(ax._range_tightbbox(x)[1] for ax in group1)
                    x2 = min(ax._range_tightbbox(x)[0] for ax in group2)
                    jspaces.append((x2 - x1)/self.dpi)
                if jspaces:
                    space = max(0, space - min(jspaces) + pad) # TODO: why max 0?
                    space = _notNone(space_orig, space) # only if user did not provide original space!!!
                jspace[i] = space
            spaces.append(jspace)

        # Apply new spaces
        subplots_kw.update({
            'wspace':spaces[0], 'hspace':spaces[1],
            })
        figsize, gridspec_kw, _ = _subplots_geometry(**subplots_kw)
        self._gridspec_main.update(**gridspec_kw)
        self.set_size_inches(figsize)

    def _align_axislabels(self, b=True):
        """Aligns spanning *x* and *y* axis labels, accounting for figure
        margins and axes and figure panels."""
        # TODO: Ensure this is robust to complex panels and shared axes
        # NOTE: Need to turn off aligned labels before _adjust_tight_layout
        # call, so cannot put this inside Axes draw
        tracker = {*()}
        for ax in self._axes_main:
            if not isinstance(ax, axes.CartesianAxes):
                continue
            for x,axis in zip('xy', (ax.xaxis, ax.yaxis)):
                s = axis.get_label_position()[0] # top or bottom, left or right
                span = getattr(ax, '_span' + x)
                align = getattr(ax, '_align' + x)
                if s not in 'bl' or axis in tracker:
                    continue
                axs = ax._get_side_axes(s)
                for _ in range(2):
                    axs = [getattr(ax, '_share' + x) or ax for ax in axs]
                # Align axis label offsets
                axises = [getattr(ax, x + 'axis') for ax in axs]
                tracker.update(axises)
                if span or align:
                    grp = getattr(self, '_align_' + x + 'label_grp', None)
                    if grp is not None:
                        for ax in axs[1:]:
                            grp.join(axs[0], ax) # copied from source code, add to grouper
                    elif align:
                        warnings.warn(f'Aligning *x* and *y* axis labels required matplotlib >=3.1.0')
                if not span:
                    continue
                # Get spanning label position
                c, spanax = self._get_align_coord(s, axs)
                spanaxis = getattr(spanax, x + 'axis')
                spanlabel = spanaxis.label
                if not hasattr(spanlabel, '_orig_transform'):
                    spanlabel._orig_transform = spanlabel.get_transform()
                    spanlabel._orig_position = spanlabel.get_position()
                if not b: # toggle off, done before tight layout
                    spanlabel.set_transform(spanlabel._orig_transform)
                    spanlabel.set_position(spanlabel._orig_position)
                    for axis in axises:
                        axis.label.set_visible(True)
                else: # toggle on, done after tight layout
                    if x == 'x':
                        position = (c, 1)
                        transform = mtransforms.blended_transform_factory(
                                self.transFigure, mtransforms.IdentityTransform())
                    else:
                        position = (1, c)
                        transform = mtransforms.blended_transform_factory(
                                mtransforms.IdentityTransform(), self.transFigure)
                    for axis in axises:
                        axis.label.set_visible((axis is spanaxis))
                    spanlabel.update({'position':position, 'transform':transform})

    def _align_suplabels(self, renderer):
        """Adjusts position of row and column labels, and aligns figure
        super title accounting for figure marins and axes and figure panels."""
        # Offset using tight bounding boxes
        # TODO: Super labels fail with popup backend!! Fix this
        # NOTE: Must use get_tightbbox so (1) this will work if tight layout
        # mode if off and (2) actually need *two* tight bounding boxes when
        # labels are present: 1 not including the labels, used to position
        # them, and 1 including the labels, used to determine figure borders
        suptitle = self._suptitle
        suptitle_on = suptitle.get_text().strip()
        width, height = self.get_size_inches()
        for s in 'lrbt':
            # Get axes and offset the label to relevant panel
            x = ('x' if s in 'lr' else 'y')
            axs = self._get_align_axes(s)
            axs = [ax._reassign_suplabel(s) for ax in axs]
            labels = [getattr(ax, '_' + s + 'label') for ax in axs]
            coords = [None]*len(axs)
            if s == 't' and suptitle_on:
                supaxs = axs
            with _hidelabels(*labels):
                for i,(ax,label) in enumerate(zip(axs,labels)):
                    label_on = label.get_text().strip()
                    if not label_on:
                        continue
                    # Get coord from tight bounding box
                    # Include twin axes and panels along the same side
                    extra = ('bt' if s in 'lr' else 'lr')
                    icoords = []
                    for iax in ax._iter_panels(extra):
                        bbox = iax.get_tightbbox(renderer)
                        if s == 'l':
                            jcoords = (bbox.xmin, 0)
                        elif s == 'r':
                            jcoords = (bbox.xmax, 0)
                        elif s == 't':
                            jcoords = (0, bbox.ymax)
                        else:
                            jcoords = (0, bbox.ymin)
                        c = self.transFigure.inverted().transform(jcoords)
                        c = (c[0] if s in 'lr' else c[1])
                        icoords.append(c)
                    # Offset, and offset a bit extra for left/right labels
                    # See: https://matplotlib.org/api/text_api.html#matplotlib.text.Text.set_linespacing
                    fontsize = label.get_fontsize()
                    if s in 'lr':
                        scale1, scale2 = 0.6, width
                    else:
                        scale1, scale2 = 0.3, height
                    if s in 'lb':
                        coords[i] = min(icoords) - (scale1*fontsize/72)/scale2
                    else:
                        coords[i] = max(icoords) + (scale1*fontsize/72)/scale2
                # Assign coords
                coords = [i for i in coords if i is not None]
                if coords:
                    if s in 'lb':
                        c = min(coords)
                    else:
                        c = max(coords)
                    for label in labels:
                        label.update({x: c})

        # Update super title position
        # If no axes on the top row are visible, do not try to align!
        if suptitle_on and supaxs:
            ys = []
            for ax in supaxs:
                bbox = ax.get_tightbbox(renderer)
                _, y = self.transFigure.inverted().transform((0, bbox.ymax))
                ys.append(y)
            x, _ = self._get_align_coord('t', supaxs)
            y = max(ys) + (0.3*suptitle.get_fontsize()/72)/height
            kw = {'x':x, 'y':y, 'ha':'center', 'va':'bottom',
                  'transform':self.transFigure}
            suptitle.update(kw)

    def _insert_row_column(self, side, idx,
        ratio, space, space_orig, figure=False,
        ):
        """Helper function that "overwrites" the main figure gridspec to make
        room for a panel. The `side` is the panel side, the `idx` is the
        slot you want the panel to occupy, and the remaining args are the
        panel widths and spacings."""
        # Constants and stuff
        # Insert spaces to the left of right panels or to the right of
        # left panels. And note that since .insert() pushes everything in
        # that column to the right, actually must insert 1 slot farther to
        # the right when inserting left panels/spaces
        s = side[0]
        if s not in 'lrbt':
            raise ValueError(f'Invalid side {side}.')
        idx_space = idx - 1*bool(s in 'br')
        idx_offset = 1*bool(s in 'tl')
        if s in 'lr':
            w, ncols = 'w', 'ncols'
        else:
            w, ncols = 'h', 'nrows'

        # Load arrays and test if we need to insert
        subplots_kw = self._subplots_kw
        subplots_orig_kw = self._subplots_orig_kw
        panels = subplots_kw[w + 'panels']
        ratios = subplots_kw[w + 'ratios']
        spaces = subplots_kw[w + 'space']
        spaces_orig = subplots_orig_kw[w + 'space']

        # Slot already exists
        entry = ('f' if figure else s)
        exists = (idx not in (-1, len(panels)) and panels[idx] == entry)
        if exists: # already exists!
            if spaces_orig[idx_space] is None:
                spaces_orig[idx_space] = units(space_orig)
            spaces[idx_space] = _notNone(spaces_orig[idx_space], space)
        # Make room for new panel slot
        else:
            # Modify basic geometry
            idx += idx_offset
            idx_space += idx_offset
            subplots_kw[ncols] += 1
            # Original space, ratio array, space array, panel toggles
            spaces_orig.insert(idx_space, space_orig)
            spaces.insert(idx_space, space)
            ratios.insert(idx, ratio)
            panels.insert(idx, entry)
            # Reference ax location array
            # TODO: For now do not need to increment, but need to double
            # check algorithm for fixing axes aspect!
            # ref = subplots_kw[x + 'ref']
            # ref[:] = [val + 1 if val >= idx else val for val in ref]

        # Update figure
        figsize, gridspec_kw, _ = _subplots_geometry(**subplots_kw)
        self.set_size_inches(figsize)
        if exists:
            gridspec = self._gridspec_main
            gridspec.update(**gridspec_kw)
        else:
            # New gridspec
            gridspec = FlexibleGridSpec(self, **gridspec_kw)
            self._gridspec_main = gridspec
            # Reassign subplotspecs to all axes and update positions
            # May seem inefficient but it literally just assigns a hidden,
            # attribute, and the creation time for subpltospecs is tiny
            axs = [iax for ax in self._iter_axes() for iax in (ax, *ax.child_axes)]
            for ax in axs:
                # Get old index
                # NOTE: Endpoints are inclusive, not exclusive!
                if not hasattr(ax, 'get_subplotspec'):
                    continue
                if s in 'lr':
                    inserts = (None, None, idx, idx)
                else:
                    inserts = (idx, idx, None, None)
                subplotspec = ax.get_subplotspec()
                igridspec = subplotspec.get_gridspec()
                topmost = subplotspec.get_topmost_subplotspec()
                # Apply new subplotspec!
                nrows, ncols, *coords = topmost.get_active_rows_columns()
                for i in range(4):
                    # if inserts[i] is not None and coords[i] >= inserts[i]:
                    if inserts[i] is not None and coords[i] >= inserts[i]:
                        coords[i] += 1
                (row1, row2, col1, col2) = coords
                subplotspec_new = gridspec[row1:row2+1, col1:col2+1]
                if topmost is subplotspec:
                    ax.set_subplotspec(subplotspec_new)
                elif topmost is igridspec._subplot_spec:
                    igridspec._subplot_spec = subplotspec_new
                else:
                    raise ValueError(f'Unexpected GridSpecFromSubplotSpec nesting.')
                # Update parent or child position
                ax.update_params()
                ax.set_position(ax.figbox)

        return gridspec

    def _get_align_coord(self, side, axs):
        """Returns figure coordinate for spanning labels and super title. The
        `x` can be ``'x'`` or ``'y'``."""
        # Get position in figure relative coordinates
        s = side[0]
        x = ('y' if s in 'lr' else 'x')
        extra = ('tb' if s in 'lr' else 'lr')
        if self._include_panels:
            axs = [iax for ax in axs for iax in ax._iter_panels(extra)]
        ranges = np.array([ax._range_gridspec(x) for ax in axs])
        min_, max_ = ranges[:,0].min(), ranges[:,1].max()
        axlo = axs[np.where(ranges[:,0] == min_)[0][0]]
        axhi = axs[np.where(ranges[:,1] == max_)[0][0]]
        lobox = axlo.get_subplotspec().get_position(self)
        hibox = axhi.get_subplotspec().get_position(self)
        if x == 'x':
            pos = (lobox.x0 + hibox.x1)/2
        else:
            pos = (lobox.y1 + hibox.y0)/2 # 'lo' is actually on top, highest up in gridspec
        # Return axis suitable for spanning position
        spanax = axs[(np.argmin(ranges[:,0]) + np.argmax(ranges[:,1]))//2]
        spanax = spanax._panel_parent or spanax
        return pos, spanax

    def _get_align_axes(self, side):
        """Returns main axes along the left, right, bottom, or top sides
        of the figure."""
        # Initial stuff
        s = side[0]
        idx = (0 if s in 'lt' else 1)
        if s in 'lr':
            x, y = 'x', 'y'
        else:
            x, y = 'y', 'x'
        # Get edge index
        axs = self._axes_main
        if not axs:
            return []
        ranges = np.array([ax._range_gridspec(x) for ax in axs])
        min_, max_ = ranges[:,0].min(), ranges[:,1].max()
        edge = (min_ if s in 'lt' else max_)
        # Return axes on edge sorted by order of appearance
        axs = [ax for ax in self._axes_main if ax._range_gridspec(x)[idx] == edge]
        ord = [ax._range_gridspec(y)[0] for ax in axs]
        return [ax for _,ax in sorted(zip(ord, axs)) if ax.get_visible()]

    def _unlock(self):
        """Prevents warning message when adding subplots one-by-one, used
        internally."""
        return _unlocker(self)

    def _update_axislabels(self, axis=None, **kwargs):
        """Applies axis labels to the relevant shared axis. If spanning
        labels are toggled, keeps the labels synced for all subplots in the
        same row or column. Label positions will be adjusted at draw-time
        with _align_axislabels."""
        x = axis.axis_name
        if x not in 'xy':
            return
        # Update label on this axes
        axis.label.update(kwargs)
        kwargs.pop('color', None)

        # Defer to parent (main) axes if possible, then get the axes
        # shared by that parent
        # TODO: Share panels in successive stacks, but share short axes
        # just like sharing long axes
        ax = axis.axes
        ax = ax._panel_parent or ax
        ax = getattr(ax, '_share' + x) or ax

        # Apply to spanning axes and their panels
        axs = [ax]
        if getattr(ax, '_span' + x):
            s = axis.get_label_position()[0]
            if s in 'lb':
                axs = ax._get_side_axes(s)
        for ax in axs:
            getattr(ax, x + 'axis').label.update(kwargs) # apply to main axes
            pax = getattr(ax, '_share' + x)
            if pax is not None: # apply to panel?
                getattr(pax, x + 'axis').label.update(kwargs)

    def _update_suplabels(self, ax, side, labels, **kwargs):
        """Assigns side labels, updates label settings."""
        s = side[0]
        if s not in 'lrbt':
            raise ValueError(f'Invalid label side {side!r}.')

        # Get main axes on the edge
        axs = self._get_align_axes(s)
        if not axs:
            return # occurs if called while adding axes

        # Update label text for axes on the edge
        if labels is None or isinstance(labels, str): # common during testing
            labels = [labels]*len(axs)
        if len(labels) != len(axs):
            raise ValueError(f'Got {len(labels)} {s}labels, but there are {len(axs)} axes along that side.')
        for ax,label in zip(axs,labels):
            obj = getattr(ax, '_' + s + 'label')
            if label is not None and obj.get_text() != label:
                obj.set_text(label)
            if kwargs:
                obj.update(kwargs)

    def _update_suptitle(self, title, **kwargs):
        """Assign figure "super title"."""
        if title is not None and self._suptitle.get_text() != title:
            self._suptitle.set_text(title)
        if kwargs:
            self._suptitle.update(kwargs)

    def add_subplot(self, *args, **kwargs):
        """Issues warning for new users that try to call
        `~matplotlib.figure.Figure.add_subplot` manually."""
        if self._locked:
            warnings.warn('Using "fig.add_subplot()" with ProPlot figures may result in unexpected behavior. Please use "proplot.subplots()" instead.')
        ax = super().add_subplot(*args, **kwargs)
        return ax

    def colorbar(self, *args,
        loc='r', width=None, space=None,
        row=None, col=None, rows=None, cols=None, span=None,
        **kwargs):
        """
        Draws a colorbar along the left, right, bottom, or top side
        of the figure, centered between the leftmost and rightmost (or
        topmost and bottommost) main axes.

        Parameters
        ----------
        loc : str, optional
            The colorbar location. Valid location keys are as follows.

            ===========  =====================
            Location     Valid keys
            ===========  =====================
            left edge    ``'l'``, ``'left'``
            right edge   ``'r'``, ``'right'``
            bottom edge  ``'b'``, ``'bottom'``
            top edge     ``'t'``, ``'top'``
            ===========  =====================

        row, rows : optional
            Aliases for `span` for panels on the left or right side.
        col, cols : optional
            Aliases for `span` for panels on the top or bottom side.
        span : int or (int, int), optional
            Describes how the colorbar spans rows and columns of subplots.
            For example, ``fig.colorbar(loc='b', col=1)`` draws a colorbar
            beneath the leftmost column of subplots, and
            ``fig.colorbar(loc='b', cols=(1,2))`` draws a colorbar beneath the
            left two columns of subplots. By default, the colorbar will span
            all rows and columns.
        space : float or str, optional
            The space between the main subplot grid and the colorbar, or the
            space between successively stacked colorbars. Units are interpreted
            by `~proplot.utils.units`. By default, this is determined by
            the "tight layout" algorithm, or is :rc:`subplots.panelspace`
            if "tight layout" is off.
        width : float or str, optional
            The colorbar width. Units are interpreted by
            `~proplot.utils.units`. Default is :rc:`colorbar.width`.
        *args, **kwargs
            Passed to `~proplot.axes.Axes.colorbar`.
        """
        if 'cax' in kwargs:
            return super().colorbar(*args, **kwargs)
        elif 'ax' in kwargs:
            return kwargs.pop('ax').colorbar(*args,
                    space=space, width=width, **kwargs)
        else:
            ax = self._add_figure_panel(loc,
                    space=space, width=width, span=span,
                    row=row, col=col, rows=rows, cols=cols)
            return ax.colorbar(*args, loc='_fill', **kwargs)

    def legend(self, *args,
        loc='r', width=None, space=None,
        row=None, col=None, rows=None, cols=None, span=None,
        **kwargs):
        """
        Draws a legend along the left, right, bottom, or top side of the
        figure, centered between the leftmost and rightmost (or
        topmost and bottommost) main axes.

        Parameters
        ----------
        loc : str, optional
            The legend location. Valid location keys are as follows.

            ===========  =====================
            Location     Valid keys
            ===========  =====================
            left edge    ``'l'``, ``'left'``
            right edge   ``'r'``, ``'right'``
            bottom edge  ``'b'``, ``'bottom'``
            top edge     ``'t'``, ``'top'``
            ===========  =====================

        row, rows : optional
            Aliases for `span` for panels on the left or right side.
        col, cols : optional
            Aliases for `span` for panels on the top or bottom side.
        span : int or (int, int), optional
            Describes how the legend spans rows and columns of subplots.
            For example, ``fig.legend(loc='b', col=1)`` draws a legend
            beneath the leftmost column of subplots, and
            ``fig.legend(loc='b', cols=(1,2))`` draws a legend beneath the
            left two columns of subplots. By default, the legend will span
            all rows and columns.
        space : float or str, optional
            The space between the main subplot grid and the legend, or the
            space between successively stacked colorbars. Units are interpreted by
            `~proplot.utils.units`. By default, this is adjusted automatically
            in the "tight layout" calculation, or is
            :rc:`subplots.panelspace` if "tight layout" is turned off.
        *args, **kwargs
            Passed to `~proplot.axes.Axes.legend`.
        """
        if 'ax' in kwargs:
            return kwargs.pop('ax').legend(*args,
                    space=space, width=width, **kwargs)
        else:
            ax = self._add_figure_panel(loc,
                    space=space, width=width, span=span,
                    row=row, col=col, rows=rows, cols=cols)
            return ax.legend(*args, loc='_fill', **kwargs)

    @_counter
    def draw(self, renderer):
        """Before drawing the figure, applies "tight layout" and aspect
        ratio-conserving adjustments, and aligns row and column labels."""
        # Renderer fixes
        # WARNING: *Critical* that draw() is invoked with the same renderer
        # FigureCanvasAgg.print_png() uses to render the image. But print_png()
        # calls get_renderer() after draw(), and get_renderer() returns a new
        # renderer if it detects renderer dims and figure dims are out of sync!
        # 1. Could use 'get_renderer' to update 'canvas.renderer' with the new
        #    figure width and height, then use that renderer for rest of draw
        #    This repair *breaks* just the *macosx* popup backend and not the
        #    qt backend! So for now just employ simple exception if this is
        #    macosx backend.
        # 2. Could set '_lastKey' on canvas and 'width' and 'height' on renderer,
        #    but then '_renderer' was initialized with wrong width and height,
        #    which causes bugs. And _renderer was generated with cython code
        # WARNING: Vector graphic renderers are another ballgame, *impossible*
        # to consistently apply successive figure size changes. SVGRenderer
        # and PDFRenderer both query the size in inches before calling draw,
        # and cannot modify PDFPage or SVG renderer props inplace, so idea was
        # to override get_size_inches. But when get_size_inches is called, the
        # canvas has no renderer, so cannot apply tight layout yet!
        for ax in self._iter_axes():
            ax._draw_auto_legends_colorbars()
        self._adjust_aspect()
        self._align_axislabels(False)
        self._align_suplabels(renderer)
        if self._auto_tight_layout:
            self._adjust_tight_layout(renderer)
        self._align_axislabels(True)
        canvas = getattr(self, 'canvas', None)
        if hasattr(canvas, 'get_renderer') and (mbackend is None or
            not isinstance(canvas, mbackend.FigureCanvasMac)):
            renderer = canvas.get_renderer()
            canvas.renderer = renderer
        return super().draw(renderer)

    def savefig(self, filename, **kwargs):
        """
        Before saving the figure, applies "tight layout" and aspect
        ratio-conserving adjustments, and aligns row and column labels.

        Parameters
        ----------
        filename : str
            The file path. User directories are automatically
            expanded, e.g. ``fig.save('~/plots/plot.png')``.
        **kwargs
            Passed to `~matplotlib.figure.Figure.savefig`.
        """
        filename = os.path.expanduser(filename)
        canvas = getattr(self, 'canvas', None)
        if hasattr(canvas, 'get_renderer'):
            renderer = canvas.get_renderer()
            canvas.renderer = renderer
            for ax in self._iter_axes():
                ax._draw_auto_legends_colorbars()
            self._adjust_aspect()
            self._align_axislabels(False)
            self._align_suplabels(renderer)
            if self._auto_tight_layout:
                self._adjust_tight_layout(renderer)
            self._align_axislabels(True)
        else:
            warnings.warn('Renderer unknown, could not adjust layout before saving.')
        super().savefig(filename, **kwargs)

    save = savefig
    """Alias for `~Figure.savefig`, because calling ``fig.savefig``
    is sort of redundant."""

    def _iter_axes(self):
        """Iterates over all axes and panels in the figure belonging to the
        `~proplot.axes.Axes` class. Excludes inset and twin axes."""
        axs = []
        for ax in (*self._axes_main, *self._lpanels, *self._rpanels,
                   *self._bpanels, *self._tpanels):
            if not ax or not ax.get_visible():
                continue
            axs.append(ax)
        for ax in axs:
            for s in 'lrbt':
                for iax in getattr(ax, '_' + s + 'panels'):
                    if not iax or not iax.get_visible():
                        continue
                    axs.append(iax)
        return axs

#-----------------------------------------------------------------------------#
# Primary plotting function, used to create figure/axes
#-----------------------------------------------------------------------------#
def _journals(journal):
    """Journal sizes for figures."""
    # Get dimensions for figure from common journals.
    value = JOURNAL_SPECS.get(journal, None)
    if value is None:
        raise ValueError(f'Unknown journal figure size specifier {journal!r}. ' +
                          'Current options are: ' + ', '.join(JOURNAL_SPECS.keys()))
    # Return width, and optionally also the height
    width, height = None, None
    try:
        width, height = value
    except (TypeError, ValueError):
        width = value
    return width, height

def _axes_dict(naxs, value, kw=False, default=None):
    """Build a dictionary that looks like ``{1:value1, 2:value2, ...}`` or
    ``{1:{key1:value1, ...}, 2:{key2:value2, ...}, ...}`` for storing
    standardized axes-specific properties or keyword args."""
    # First build up dictionary
    # 1) 'string' or {1:'string1', (2,3):'string2'}
    if not kw:
        if np.iterable(value) and not isinstance(value, (str,dict)):
            value = {num+1: item for num,item in enumerate(value)}
        elif not isinstance(value, dict):
            value = {range(1, naxs+1): value}
    # 2) {'prop':value} or {1:{'prop':value1}, (2,3):{'prop':value2}}
    else:
        nested = [isinstance(value,dict) for value in value.values()]
        if not any(nested): # any([]) == False
            value = {range(1, naxs+1): value.copy()}
        elif not all(nested):
            raise ValueError('Pass either of dictionary of key value pairs or a dictionary of dictionaries of key value pairs.')
    # Then *unfurl* keys that contain multiple axes numbers, i.e. are meant
    # to indicate properties for multiple axes at once
    kwargs = {}
    for nums,item in value.items():
        nums = np.atleast_1d(nums)
        for num in nums.flat:
            if not kw:
                kwargs[num] = item
            else:
                kwargs[num] = item.copy()
    # Fill with default values
    for num in range(1, naxs+1):
        if num not in kwargs:
            if kw:
                kwargs[num] = {}
            else:
                kwargs[num] = default
    # Verify numbers
    if {*range(1, naxs+1)} != {*kwargs.keys()}:
        raise ValueError(f'Have {naxs} axes, but {value} has properties for axes {", ".join(str(i) for i in sorted(kwargs.keys()))}.')
    return kwargs

def subplots(array=None, ncols=1, nrows=1,
    ref=1, order='C',
    aspect=1, figsize=None,
    width=None,  height=None, axwidth=None, axheight=None, journal=None,
    hspace=None, wspace=None, space=None,
    hratios=None, wratios=None,
    width_ratios=None, height_ratios=None,
    flush=None, wflush=None, hflush=None,
    left=None, bottom=None, right=None, top=None,
    tight=None, pad=None, axpad=None, panelpad=None,
    span=None, spanx=None, spany=None,
    align=None, alignx=None, aligny=None,
    share=None, sharex=None, sharey=None,
    basemap=False, proj=None, projection=None, proj_kw=None, projection_kw=None,
    autoformat=True, includepanels=False,
    ):
    """
    Analogous to `matplotlib.pyplot.subplots`, creates a figure with a single
    axes or arbitrary grids of axes, any of which can be map projections,
    and optional "panels" along axes or figure edges.

    The parameters are sorted into the following rough sections: subplot grid
    specifications, figure and subplot sizes, axis sharing,
    figure panels, axes panels, and map projections.

    Parameters
    ----------
    array : array-like of int, optional
        2-dimensional array specifying complex grid of subplots. Think of
        this array as a "picture" of your figure. For example, the array
        ``[[1, 1], [2, 3]]`` creates one long subplot in the top row, two
        smaller subplots in the bottom row. Integers must range from 1 to the
        number of plots.

        ``0`` indicates an empty space. For example, ``[[1, 1, 1], [2, 0, 3]]``
        creates one long subplot in the top row with two subplots in the bottom
        row separated by a space.
    ncols, nrows : int, optional
        Number of columns, rows. Ignored if `array` is not ``None``.
        Use these arguments for simpler subplot grids.
    order : {'C', 'F'}, optional
        Whether subplots are numbered in column-major (``'C'``) or row-major
        (``'F'``) order. Analogous to `numpy.array` ordering. This controls
        the order axes appear in the `axs` list, and the order of subplot
        a-b-c labeling (see `~proplot.axes.Axes.format`).
    figsize : length-2 tuple, optional
        Tuple specifying the figure `(width, height)`.
    width, height : float or str, optional
        The figure width and height. Units are interpreted by
        `~proplot.utils.units`.
    journal : str, optional
        String name corresponding to an academic journal standard that is used
        to control the figure width (and height, if specified). Valid names
        are described in a table below.

    ref : int, optional
        The reference axes number. The `axwidth`, `axheight`, and `aspect`
        keyword args are applied to this axes, and aspect ratio is conserved
        for this axes in tight layout adjustment.
    axwidth, axheight : float or str, optional
        Sets the average width, height of your axes. Units are interpreted by
        `~proplot.utils.units`. Default is :rc:`subplots.axwidth`.

        These arguments are convenient where you don't care about the figure
        dimensions and just want your axes to have enough "room".
    aspect : float or length-2 list of floats, optional
        The (average) axes aspect ratio, in numeric form (width divided by
        height) or as (width, height) tuple. If you do not provide
        the `hratios` or `wratios` keyword args, all axes will have
        identical aspect ratios.
    hratios, wratios
        Aliases for `height_ratios`, `width_ratios`.
    width_ratios, height_ratios : float or list thereof, optional
        Passed to `FlexibleGridSpec`. The width
        and height ratios for the subplot grid. Length of `width_ratios`
        must match the number of rows, and length of `height_ratios` must
        match the number of columns.
    wspace, hspace, space : float or str or list thereof, optional
        Passed to `FlexibleGridSpec`, denotes the
        spacing between grid columns, rows, and both, respectively. If float
        or string, expanded into lists of length ``ncols-1`` (for `wspace`)
        or length ``nrows-1`` (for `hspace`).

        Units are interpreted by `~proplot.utils.units` for each element of
        the list. By default, these are determined by the "tight
        layout" algorithm.
    left, right, top, bottom : float or str, optional
        Passed to `FlexibleGridSpec`, denote the width of padding between the
        subplots and the figure edge. Units are interpreted by
        `~proplot.utils.units`. By default, these are determined by the
        "tight layout" algorithm.

    sharex, sharey, share : {3, 2, 1, 0}, optional
        The "axis sharing level" for the *x* axis, *y* axis, or both axes.
        Default is ``3``. This can considerably redundancy in your figure.
        Options are as follows:

        0. No axis sharing. Also sets the default `spanx` and `spany` values
           to ``False``.
        1. Only draw *axis label* on the leftmost column (*y*) or
           bottommost row (*x*) of subplots. Axis tick labels
           still appear on every subplot.
        2. As in 1, but forces the axis limits to be identical. Axis
           tick labels still appear on every subplot.
        3. As in 2, but only show the *axis tick labels* on the
           leftmost column (*y*) or bottommost row (*x*) of subplots.

    spanx, spany, span : bool or {0, 1}, optional
        Default is ``False`` if `sharex`, `sharey`, or `share` are ``0``,
        ``True`` otherwise. Toggles "spanning" axis labels for the *x* axis,
        *y* axis, or both axes. When ``True``, a single, centered axis label
        is used for all axes with bottom and left edges in the same row or
        column.  This can considerably redundancy in your figure.

        "Spanning" labels integrate with "shared" axes. For example,
        for a 3-row, 3-column figure, with ``sharey > 1`` and ``spany=1``,
        your figure will have 1 ylabel instead of 9.
    alignx, aligny, align : bool or {0, 1}, optional
        Default is ``False``. Whether to `align axis labels
        <https://matplotlib.org/3.1.1/gallery/subplots_axes_and_figures/align_labels_demo.html>`__
        for the *x* axis, *y* axis, or both axes. Only has an effect when
        `spanx`, `spany`, or `span` are ``False``.
    proj, projection : str or dict-like, optional
        The map projection name. The argument is interpreted as follows.

        * If string, this projection is used for all subplots. For valid
          names, see the :ref:`Table of projections`.
        * If list of string, these are the projections to use for each
          subplot in their `array` order.
        * If dict-like, keys are integers or tuple integers that indicate
          the projection to use for each subplot. If a key is not provided,
          that subplot will be a `~proplot.axes.CartesianAxes`. For example,
          in a 4-subplot figure, ``proj={2:'merc', (3,4):'stere'}``
          draws a Cartesian axes for the first subplot, a Mercator
          projection for the second subplot, and a Stereographic projection
          for the second and third subplots.

    proj_kw, projection_kw : dict-like, optional
        Keyword arguments passed to `~mpl_toolkits.basemap.Basemap` or
        cartopy `~cartopy.crs.Projection` classes on instantiation.
        If dictionary of properties, applies globally. If *dictionary of
        dictionaries* of properties, applies to specific subplots, as
        with `proj`.

        For example, with ``ncols=2`` and
        ``proj_kw={1:{'lon_0':0}, 2:{'lon_0':180}}``, the projection in
        the left subplot is centered on the prime meridian, and the projection
        in the right subplot is centered on the international dateline.
    basemap : bool or dict-like, optional
        Whether to use `~mpl_toolkits.basemap.Basemap` or
        `~cartopy.crs.Projection` for map projections. Default is ``False``.
        If boolean, applies to all subplots. If dictionary, values apply to
        specific subplots, as with `proj`.

    Other parameters
    ----------------
    tight : bool, optional
        Toggles automatic tight layout adjustments. Default is
        :rc:`tight`.

        If you manually specify a spacing, it will be used
        to override the tight layout spacing -- for example, with ``left=0.1``,
        the left margin is set to 0.1 inches wide, while the remaining margin
        widths are calculated automatically.
    pad, axpad, panelpad : float or str, optional
        Padding for automatic tight layout adjustments. See `Figure` for
        details.
    includepanels : bool, optional
        Whether to include panels when calculating the position of certain
        spanning labels. See `Figure` for details.
    autoformat : bool, optional
        Whether to automatically format axes when special datasets are
        passed to plotting commands. See `Figure` for details.

    Returns
    -------
    f : `Figure`
        The figure instance.
    axs : `axes_grid`
        A special list of axes instances. See `axes_grid`.


    Current options for the `journal` keyword argument are as follows.
    If you'd like to add additional standards, feel free to submit a pull request

    ===========  ====================  ==========================================================================================================================================================
    Key          Size description      Organization
    ===========  ====================  ==========================================================================================================================================================
    ``'pnas1'``  1-column              `Proceedings of the National Academy of Sciences <http://www.pnas.org/page/authors/submission>`__
    ``'pnas2'``  2-column              ”
    ``'pnas3'``  landscape page        ”
    ``'ams1'``   1-column              `American Meteorological Society <https://www.ametsoc.org/ams/index.cfm/publications/authors/journal-and-bams-authors/figure-information-for-authors/>`__
    ``'ams2'``   small 2-column        ”
    ``'ams3'``   medium 2-column       ”
    ``'ams4'``   full 2-column         ”
    ``'agu1'``   1-column              `American Geophysical Union <https://publications.agu.org/author-resource-center/figures-faq/>`__
    ``'agu2'``   2-column              ”
    ``'agu3'``   full height 1-column  ”
    ``'agu4'``   full height 2-column  ”
    ===========  ====================  ==========================================================================================================================================================
    """
    rc._getitem_mode = 0 # ensure still zero; might be non-zero if had error in 'with context' block
    # Build array
    if order not in ('C','F'): # better error message
        raise ValueError(f'Invalid order {order!r}. Choose from "C" (row-major, default) and "F" (column-major).')
    if array is None:
        array = np.arange(1,nrows*ncols+1)[...,None]
        array = array.reshape((nrows, ncols), order=order)
    # Standardize array
    try:
        array = np.array(array, dtype=int) # enforce array type
        if array.ndim == 1:
            array = array[None,:] if order == 'C' else array[:,None] # interpret as single row or column
        elif array.ndim != 2:
            raise ValueError
        array[array == None] = 0 # use zero for placeholder
    except (TypeError,ValueError):
        raise ValueError(f'Invalid subplot array {array!r}. Must be 1D or 2D array of integers.')
    # Get other props
    nums = np.unique(array[array != 0])
    naxs = len(nums)
    if {*nums.flat} != {*range(1, naxs+1)}:
        raise ValueError('Invalid subplot array {array!r}. Numbers must span integers 1 to naxs (i.e. cannot skip over numbers), with 0 representing empty spaces.')
    if ref not in nums:
        raise ValueError(f'Invalid reference number {ref!r}. For array {array!r}, must be one of {nums}.')
    nrows, ncols = array.shape

    # Figure out rows and columns "spanned" by each axes in list, for
    # axis sharing and axis label spanning settings
    sharex = int(_notNone(sharex, share, rc['share']))
    sharey = int(_notNone(sharey, share, rc['share']))
    if sharex not in range(4) or sharey not in range(4):
        raise ValueError(f'Axis sharing level can be 0 (no sharing), 1 (sharing, but keep all tick labels), and 2 (sharing, but only keep one set of tick labels). Got sharex={sharex} and sharey={sharey}.')
    spanx = _notNone(spanx, span, 0 if sharex == 0 else None, rc['span'])
    spany = _notNone(spany, span, 0 if sharey == 0 else None, rc['span'])
    alignx = _notNone(alignx, align)
    aligny = _notNone(aligny, align)
    if (spanx and alignx) or (spany and aligny):
        warnings.warn(f'The "alignx" and "aligny" args have no effect when "spanx" and "spany" are True.')
    alignx = _notNone(alignx, rc['align'])
    aligny = _notNone(alignx, rc['align'])
    # Get some axes properties, where locations are sorted by axes id.
    # NOTE: These ranges are endpoint exclusive, like a slice object!
    axids = [np.where(array == i) for i in np.sort(np.unique(array)) if i > 0] # 0 stands for empty
    xrange = np.array([[x.min(), x.max()] for _,x in axids])
    yrange = np.array([[y.min(), y.max()] for y,_ in axids]) # range accounting for panels
    xref = xrange[ref-1,:] # range for reference axes
    yref = yrange[ref-1,:]

    # Get basemap.Basemap or cartopy.crs.Projection instances for map
    proj = _notNone(projection, proj, None, names=('projection', 'proj'))
    proj_kw = _notNone(projection_kw, proj_kw, {}, names=('projection_kw', 'proj_kw'))
    proj    = _axes_dict(naxs, proj, kw=False, default='cartesian')
    proj_kw = _axes_dict(naxs, proj_kw, kw=True)
    basemap = _axes_dict(naxs, basemap, kw=False, default=False)
    axes_kw = {num:{} for num in range(1, naxs+1)}  # stores add_subplot arguments
    for num,name in proj.items():
        # The default is CartesianAxes
        if name is None or name == 'cartesian':
            axes_kw[num]['projection'] = 'cartesian'
        # Builtin matplotlib polar axes, just use my overridden version
        elif name == 'polar':
            axes_kw[num]['projection'] = 'polar2'
            if num == ref:
                aspect = 1
        # Custom Basemap and Cartopy axes
        else:
            package = 'basemap' if basemap[num] else 'cartopy'
            obj, iaspect = projs.Proj(name, basemap=basemap[num], **proj_kw[num])
            if num == ref:
                aspect = iaspect
            axes_kw[num].update({'projection':package, 'map_projection':obj})

    #-------------------------------------------------------------------------#
    # Figure architecture
    #-------------------------------------------------------------------------#
    # Figure and/or axes dimensions
    names, values = (), ()
    if journal:
        figsize = _journals(journal) # if user passed width=<string > , will use that journal size
        spec = f'journal={journal!r}'
        names = ('axwidth', 'axheight', 'width')
        values = (axwidth, axheight, width)
        width, height = figsize
    elif figsize:
        spec = f'figsize={figsize!r}'
        names = ('axwidth', 'axheight', 'width', 'height')
        values = (axwidth, axheight, width, height)
        width, height = figsize
    elif width is not None or height is not None:
        spec = []
        if width is not None:
            spec.append(f'width={width!r}')
        if height is not None:
            spec.append(f'height={height!r}')
        spec = ', '.join(spec)
        names = ('axwidth', 'axheight')
        values = (axwidth, axheight)
    # Raise warning
    for name,value in zip(names,values):
        if value is not None:
            warnings.warn(f'You specified both {spec} and {name}={value!r}. Ignoring {name!r}.')

    # Standardized dimensions
    width, height = units(width), units(height)
    axwidth, axheight = units(axwidth), units(axheight)
    # Standardized user input border spaces
    left, right = units(left), units(right)
    bottom, top = units(bottom), units(top)
    # Standardized user input spaces
    wspace = np.atleast_1d(units(_notNone(wspace, space)))
    hspace = np.atleast_1d(units(_notNone(hspace, space)))
    if len(wspace) == 1:
        wspace = np.repeat(wspace, (ncols-1,))
    if len(wspace) != ncols-1:
        raise ValueError(f'Require {ncols-1} width spacings for {ncols} columns, got {len(wspace)}.')
    if len(hspace) == 1:
        hspace = np.repeat(hspace, (nrows-1,))
    if len(hspace) != nrows-1:
        raise ValueError(f'Require {nrows-1} height spacings for {nrows} rows, got {len(hspace)}.')
    # Standardized user input ratios
    wratios = np.atleast_1d(_notNone(width_ratios, wratios, 1,
        names=('width_ratios', 'wratios')))
    hratios = np.atleast_1d(_notNone(height_ratios, hratios, 1,
        names=('height_ratios', 'hratios')))
    if len(wratios) == 1:
        wratios = np.repeat(wratios, (ncols,))
    if len(hratios) == 1:
        hratios = np.repeat(hratios, (nrows,))
    if len(wratios) != ncols:
        raise ValueError(f'Got {ncols} columns, but {len(wratios)} wratios.')
    if len(hratios) != nrows:
        raise ValueError(f'Got {nrows} rows, but {len(hratios)} hratios.')

    # Fill subplots_orig_kw with user input values
    # NOTE: 'Ratios' are only fixed for panel axes, but we store entire array
    wspace, hspace = wspace.tolist(), hspace.tolist()
    wratios, hratios = wratios.tolist(), hratios.tolist()
    subplots_orig_kw = {
        'left':left, 'right':right, 'top':top, 'bottom':bottom,
        'wspace':wspace, 'hspace':hspace,
        }

    # Default border spaces
    left = _notNone(left, units(rc['subplots.ylabspace']))
    right = _notNone(right, units(rc['subplots.innerspace']))
    top = _notNone(top, units(rc['subplots.titlespace']))
    bottom = _notNone(bottom, units(rc['subplots.xlabspace']))
    # Default spaces between axes
    wratios, hratios = [*wratios], [*hratios] # copies
    wspace, hspace = np.array(wspace), np.array(hspace) # also copies!
    wspace[wspace==None] = (
        units(rc['subplots.innerspace']) if sharey == 3
        else units(rc['subplots.ylabspace']) - units(rc['subplots.titlespace']) if sharey in (1,2) # space for tick labels only
        else units(rc['subplots.ylabspace']))
    hspace[hspace==None] = (
        units(rc['subplots.titlespace']) + units(rc['subplots.innerspace']) if sharex == 3
        else units(rc['subplots.xlabspace']) if sharex in (1,2) # space for tick labels and title
        else units(rc['subplots.titlespace']) + units(rc['subplots.xlabspace'])
        )
    wspace, hspace = wspace.tolist(), hspace.tolist()

    # Parse arguments, fix dimensions in light of desired aspect ratio
    figsize, gridspec_kw, subplots_kw = _subplots_geometry(
        nrows=nrows, ncols=ncols,
        aspect=aspect, xref=xref, yref=yref,
        left=left, right=right, bottom=bottom, top=top,
        width=width, height=height, axwidth=axwidth, axheight=axheight,
        wratios=wratios, hratios=hratios, wspace=wspace, hspace=hspace,
        wpanels=['']*ncols, hpanels=['']*nrows,
        )
    fig = plt.figure(FigureClass=Figure, tight=tight, figsize=figsize, ref=ref,
        pad=pad, axpad=axpad, panelpad=panelpad, autoformat=autoformat,
        includepanels=includepanels,
        subplots_orig_kw=subplots_orig_kw, subplots_kw=subplots_kw,
        gridspec_kw=gridspec_kw)
    gridspec = fig._gridspec_main

    #-------------------------------------------------------------------------#
    # Draw on figure
    #-------------------------------------------------------------------------#
    # Draw main subplots
    axs = naxs*[None] # list of axes
    for idx in range(naxs):
        # Get figure gridspec ranges
        num = idx + 1
        x0, x1 = xrange[idx,0], xrange[idx,1]
        y0, y1 = yrange[idx,0], yrange[idx,1]
        # Draw subplot
        subplotspec = gridspec[y0:y1+1, x0:x1+1]
        with fig._unlock():
            axs[idx] = fig.add_subplot(subplotspec, number=num,
                spanx=spanx, spany=spany, alignx=alignx, aligny=aligny,
                sharex=sharex, sharey=sharey,
                main=True,
                **axes_kw[num])

    # Return figure and axes
    n = (ncols if order == 'C' else nrows)
    return fig, axes_grid(axs, n=n, order=order)

