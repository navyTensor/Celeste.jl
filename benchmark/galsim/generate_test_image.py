import collections
import hashlib
import logging
import os
import sys

import astropy.io.fits
import galsim

_logger = logging.getLogger(__name__) 

RANDOM_SEED = 1234
FITS_COMMENT_PREPEND = 'Celeste: '

ARCSEC_PER_DEGREE = 3600.
COUNTS_PER_NMGY = 1000.0 # a.k.a. "iota" in Celeste

# intensity (flux) relative to third band (= "a" band = reference)
# see GalsimBenchmark.typical_band_relative_intensities()
# these are taken from the current dominant component of the lognormal prior on c_s for stars
DEFAULT_STAR_RELATIVE_INTENSITIES = [
    0.1330,
    0.5308,
    1,
    1.3179,
    1.5417,
]
# these are taken from the current dominant component of the lognormal prior on c_s for galaxies
DEFAULT_GALAXY_RELATIVE_INTENSITIES = [
    0.4013,
    0.4990,
    1,
    1.4031,
    1.7750,
]

class ImageParameters(object):
    def __init__(self):
        self.width_px = 96
        self.height_px = 96
        # 0.396 = resolution of SDSS images (https://github.com/jeff-regier/Celeste.jl/pull/411)
        self.arcsec_per_pixel = 0.396
        self.origin_right_ascension_arcsec = 0
        self.origin_declination_arcsec = 0

    def degrees_per_pixel(self):
        return self.arcsec_per_pixel / ARCSEC_PER_DEGREE

    def get_world_origin_degrees(self):
        return galsim.PositionD(
            self.origin_right_ascension_arcsec / ARCSEC_PER_DEGREE,
            self.origin_declination_arcsec / ARCSEC_PER_DEGREE,
        )

    def get_image_center_world_coordinates(self):
        origin_deg = self.get_world_origin_degrees()
        width_deg = self.width_px * self.degrees_per_pixel()
        height_deg = self.height_px * self.degrees_per_pixel()
        return (origin_deg[0] + width_deg / 2.0, origin_deg[1] + height_deg / 2.0)

class AbsolutePosition(object):
    def __init__(self, right_ascension_deg, declination_deg):
        self.right_ascension_deg = right_ascension_deg
        self.declination_deg = declination_deg

    def get_position_deg(self, image_parameters):
        return (self.right_ascension_deg, self.declination_deg)

class OffsetFromCenterPosition(object):
    def __init__(self, x_offset_deg, y_offset_deg):
        self.x_offset_deg = x_offset_deg
        self.y_offset_deg = y_offset_deg

    def get_position_deg(self, image_parameters):
        image_center_deg = image_parameters.get_image_center_world_coordinates()
        return (image_center_deg[0] + self.x_offset_deg, image_center_deg[1] + self.y_offset_deg)

# fields and logic shared between stars and galaxies
class CommonFields(object):
    def __init__(self):
        self.position = OffsetFromCenterPosition(0, 0) # or an AbsolutePosition
        self.reference_band_flux_nmgy = 40
        # relative flux in each band defines "color" of light sources
        self._flux_relative_to_reference_band = DEFAULT_GALAXY_RELATIVE_INTENSITIES

    def set_offset_from_center_arcsec(self, x_offset_arcsec, y_offset_arcsec):
        self.position = OffsetFromCenterPosition(
            x_offset_arcsec / ARCSEC_PER_DEGREE,
            y_offset_arcsec / ARCSEC_PER_DEGREE,
        )

    def set_world_coordinates_deg(self, right_ascension_deg, declination_deg):
        self.position = AbsolutePosition(right_ascension_deg, declination_deg)

    def get_world_coordinates_deg(self, image_parameters):
        return self.position.get_position_deg(image_parameters)

    def set_flux_relative_to_reference_band(self, relative_flux):
        assert len(relative_flux) == 5
        assert relative_flux[2] == 1
        self._flux_relative_to_reference_band = relative_flux

    def get_flux_counts(self, band_index):
        return (
            self.reference_band_flux_nmgy * self._flux_relative_to_reference_band[band_index]
            * COUNTS_PER_NMGY
        )

    def add_header_fields(self, header, index_str, image_parameters, star_or_galaxy):
        position_deg = self.position.get_position_deg(image_parameters)
        header['CLRA' + index_str] = (position_deg[0], 'Center right ascension, deg')
        header['CLDEC' + index_str] = (position_deg[1], 'Center declination, deg')
        header['CLFLX' + index_str] = (
            self.reference_band_flux_nmgy,
            'reference (=3) band brightness (nMgy)',
        )
        header['CLC12' + index_str] = (
            self._flux_relative_to_reference_band[1] / self._flux_relative_to_reference_band[0],
            'ratio of flux in band 2 to band 1',
        )
        header['CLC23' + index_str] = (
            1 / self._flux_relative_to_reference_band[1],
            'ratio of flux in band 3 to band 2',
        )
        header['CLC34' + index_str] = (
            self._flux_relative_to_reference_band[3],
            'ratio of flux in band 4 to band 3',
        )
        header['CLC45' + index_str] = (
            self._flux_relative_to_reference_band[4] / self._flux_relative_to_reference_band[3],
            'ratio of flux in band 5 to band 4',
        )
        header['CLTYP' + index_str] = (star_or_galaxy, '"star" or "galaxy"?')

# this essentially just serves a documentation purpose
class LightSource(object):
    def get_galsim_light_source(self, band_index):
        raise NotImplementedError

    def add_header_fields(self, header, index_str, image_parameters):
        raise NotImplementedError

class Star(LightSource):
    def __init__(self):
        self._common_fields = CommonFields()
        self._common_fields.set_flux_relative_to_reference_band(DEFAULT_STAR_RELATIVE_INTENSITIES)

    def offset_arcsec(self, x, y):
        self._common_fields.set_offset_from_center_arcsec(x, y)
        return self

    def world_coordinates_deg(self, right_ascension_deg, declination_deg):
        self._common_fields.set_world_coordinates_deg(right_ascension_deg, declination_deg)
        return self

    def reference_band_flux_nmgy(self, flux):
        self._common_fields.reference_band_flux_nmgy = flux
        return self

    def flux_relative_to_reference_band(self, relative_flux):
        self._common_fields.set_flux_relative_to_reference_band(relative_flux)
        return self

    def get_galsim_light_source(self, band_index, psf_sigma_degrees, image_parameters):
        flux_counts = self._common_fields.get_flux_counts(band_index)
        return (
            galsim.Gaussian(flux=flux_counts, sigma=psf_sigma_degrees)
                .shift(self._common_fields.get_world_coordinates_deg(image_parameters))
        )

    def add_header_fields(self, header, index_str, image_parameters):
        self._common_fields.add_header_fields(header, index_str, image_parameters, 'star')

class Galaxy(LightSource):
    def __init__(self):
        self._common_fields = CommonFields()
        self._common_fields.reference_band_flux_nmgy = 10
        self._angle_deg = 0
        self._minor_major_axis_ratio = 0.4
        self._half_light_radius_arcsec = 1.5
        self._de_vaucouleurs_mixture_weight = 0.0

    def offset_arcsec(self, x, y):
        self._common_fields.set_offset_from_center_arcsec(x, y)
        return self

    def world_coordinates_deg(self, right_ascension_deg, declination_deg):
        self._common_fields.set_world_coordinates_deg(right_ascension_deg, declination_deg)
        return self

    def reference_band_flux_nmgy(self, flux):
        self._common_fields.reference_band_flux_nmgy = flux
        return self

    def flux_relative_to_reference_band(self, relative_flux):
        self._common_fields.set_flux_relative_to_reference_band(relative_flux)
        return self

    def angle_deg(self, angle):
        self._angle_deg = angle
        return self

    def minor_major_axis_ratio(self, ratio):
        self._minor_major_axis_ratio = ratio
        return self

    def half_light_radius_arcsec(self, radius):
        self._half_light_radius_arcsec = radius
        return self

    def de_vaucouleurs_mixture_weight(self, weight):
        self._de_vaucouleurs_mixture_weight = weight
        return self

    def get_galsim_light_source(self, band_index, psf_sigma_degrees, image_parameters):
        def apply_shear_and_shift(galaxy):
            return (
                galaxy.shear(q=self._minor_major_axis_ratio, beta=self._angle_deg * galsim.degrees)
                    .shift(self._common_fields.get_world_coordinates_deg(image_parameters))
            )

        flux_counts = self._common_fields.get_flux_counts(band_index)
        half_light_radius_deg = self._half_light_radius_arcsec / ARCSEC_PER_DEGREE
        exponential_profile = apply_shear_and_shift(
            galsim.Exponential(
                half_light_radius=half_light_radius_deg,
                flux=flux_counts * (1 - self._de_vaucouleurs_mixture_weight),
            )
        )
        de_vaucouleurs_profile = apply_shear_and_shift(
            galsim.DeVaucouleurs(
                half_light_radius=half_light_radius_deg,
                flux=flux_counts * self._de_vaucouleurs_mixture_weight,
            )
        )
        galaxy = exponential_profile + de_vaucouleurs_profile
        psf = galsim.Gaussian(flux=1, sigma=psf_sigma_degrees)
        return galsim.Convolve([galaxy, psf])

    def add_header_fields(self, header, index_str, image_parameters):
        self._common_fields.add_header_fields(header, index_str, image_parameters, 'galaxy')
        header['CLANG' + index_str] = (self._angle_deg, 'major axis angle (degrees from x-axis)')
        header['CLRTO' + index_str] = (self._minor_major_axis_ratio, 'minor/major axis ratio')
        header['CLRDA' + index_str] = (
            self._half_light_radius_arcsec,
            'half-light radius (arcsec)',
        )
        header['CLRDP' + index_str] = (
            self._half_light_radius_arcsec / image_parameters.arcsec_per_pixel,
            'half-light radius (pixels)',
        )
        header['CLDEV' + index_str] = (
            self._de_vaucouleurs_mixture_weight,
            'de Vaucouleurs mixture weight',
        )

# A complete description of a GalSim test image, along with logic to generate the image and the
# "ground truth" header fields
class GalSimTestCase(object):
    def __init__(self):
        self._light_sources = []
        self.image_parameters = ImageParameters()
        self.psf_sigma_pixels = 4
        self.sky_level_nmgy = 0.01
        self.include_noise = False
        self.comment = None

    def set_dimensions(self, width_px, height_px):
        self.image_parameters.width_px = width_px
        self.image_parameters.height_px = height_px

    def set_resolution(self, arcsec_per_pixel):
        self.image_parameters.arcsec_per_pixel = arcsec_per_pixel

    def set_world_origin(self, right_ascension_arcsec, declination_arcsec):
        self.image_parameters.origin_right_ascension_arcsec = right_ascension_arcsec
        self.image_parameters.origin_declination_arcsec = declination_arcsec

    def get_resolution(self):
        return self.image_parameters.arcsec_per_pixel

    def add_star(self):
        star = Star()
        self._light_sources.append(star)
        return star

    def add_galaxy(self):
        galaxy = Galaxy()
        self._light_sources.append(galaxy)
        return galaxy

    def _add_sky_background(self, image, uniform_deviate):
        sky_level_counts = self.sky_level_nmgy * COUNTS_PER_NMGY
        if self.include_noise:
            poisson_deviate = galsim.PoissonDeviate(uniform_deviate, mean=sky_level_counts)
            image.addNoise(galsim.DeviateNoise(poisson_deviate))
        else:
            image.array[:] = image.array + sky_level_counts

    def construct_image(self, band_index, uniform_deviate):
        world_origin = self.image_parameters.get_world_origin_degrees()
        wcs = (
            galsim.PixelScale(self.image_parameters.degrees_per_pixel())
                .withOrigin(galsim.PositionI(0, 0), world_origin=world_origin)
        )
        image = galsim.ImageF(
            self.image_parameters.width_px,
            self.image_parameters.height_px,
            wcs=wcs,
        )
        for index, light_source in enumerate(self._light_sources):
            sys.stdout.write('Band {} source {}\r'.format(band_index + 1, index + 1))
            sys.stdout.flush()
            galsim_light_source = light_source.get_galsim_light_source(
                band_index,
                self.psf_sigma_pixels * self.image_parameters.degrees_per_pixel(),
                self.image_parameters,
            )
            galsim_light_source.drawImage(
                image,
                add_to_image=True,
                method='phot',
                max_extra_noise=self.sky_level_nmgy * COUNTS_PER_NMGY / 1000.0,
                rng=uniform_deviate,
            )
        self._add_sky_background(image, uniform_deviate)
        return image

    def get_fits_header(self, case_index, band_index):
        # FITS header fields will be too long if there's a four-digit index
        assert len(self._light_sources) < 1000
        header = collections.OrderedDict([
            ('CLCASEI', (case_index + 1, 'test case index')),
            ('CLDESCR', (self.comment, 'comment')),
            ('CLIOTA', (COUNTS_PER_NMGY, 'counts per nMgy')),
            ('CLSKY', (self.sky_level_nmgy, '"epsilon" sky level (nMgy each px)')),
            ('CLNOISE', (self.include_noise, 'was Poisson noise added?')),
            ('CLSIGMA', (self.psf_sigma_pixels, 'Gaussian PSF sigma (px)')),
            ('CLBAND', (band_index + 1, 'color band')),
            ('CLNSRC', (len(self._light_sources), 'number of sources')),
            ('CLRES', (self.image_parameters.degrees_per_pixel(), 'resolution (degrees/px)')),
        ])
        for source_index, light_source in enumerate(self._light_sources):
            index_str = '{:03d}'.format(source_index + 1)
            light_source.add_header_fields(header, index_str, self.image_parameters)
        return header

def add_header_to_hdu(hdu, header_dict):
    header = galsim.fits.FitsHeader(hdu.header)
    for name, (value, comment) in header_dict.iteritems():
        header[name] = (value, FITS_COMMENT_PREPEND + comment)

def ensure_containing_directory_exists(filename):
    if not os.path.exists(os.path.dirname(filename)):
        os.mkdir(os.path.dirname(filename))

def save_multi_extension_fits(hdu_list, filename):
    ensure_containing_directory_exists(filename)
    if os.path.exists(filename):
        os.remove(filename)
    hdu_list.writeto(filename)

def append_md5sum_to_filename(filename):
    md5 = hashlib.md5()
    with open(filename, 'rb') as stream:
        md5.update(stream.read())
    md5sum = md5.hexdigest()
    root, extension = os.path.splitext(filename)
    new_filename = '{}_{}{}'.format(root, md5.hexdigest()[:10], extension)
    os.rename(filename, new_filename)
    return new_filename

def write_latest_filename(output_label, latest_filename):
    latest_fits_filename_holder = os.path.join(
        'latest_filenames', 'latest_{}.txt'.format(output_label)
    )
    ensure_containing_directory_exists(latest_fits_filename_holder)
    with open(latest_fits_filename_holder, 'w') as stream:
        stream.write(latest_filename)
        stream.write('\n')
    _logger.info('Updated %r', latest_fits_filename_holder)

def generate_fits_file(output_label, test_case_callbacks):
    _logger.info('Generating %d test cases', len(test_case_callbacks))
    fits_hdus = astropy.io.fits.HDUList()
    for case_index, test_case_fn in enumerate(test_case_callbacks):
        _logger.info('  Generating case %s', test_case_fn.__name__)
        uniform_deviate = galsim.UniformDeviate(RANDOM_SEED + case_index)
        test_case = GalSimTestCase()
        test_case_fn(test_case)
        for band_index in xrange(5):
            image = test_case.construct_image(band_index, uniform_deviate)
            galsim.fits.write(image, hdu_list=fits_hdus)
            fits_hdus[-1].name = '{}_{}'.format(test_case_fn.__name__, band_index + 1)
            add_header_to_hdu(fits_hdus[-1], test_case.get_fits_header(case_index, band_index))

    image_file_name = os.path.join('output', output_label + '.fits')
    save_multi_extension_fits(fits_hdus, image_file_name)
    final_filename = append_md5sum_to_filename(image_file_name)
    _logger.info('Wrote multi-extension FITS file to %r', final_filename)
    write_latest_filename(output_label, os.path.basename(final_filename))
