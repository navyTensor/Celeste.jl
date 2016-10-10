"""
Calculate value, gradient, and hessian of the variational ELBO.
"""
module DeterministicVI

using ..Model
import ..Model: BivariateNormalDerivatives, BvnComponent, GalaxyCacheComponent,
                GalaxySigmaDerivs,
                get_bvn_cov, eval_bvn_pdf!, get_bvn_derivs!,
                transform_bvn_derivs!
using ..SensitiveFloats
import ..SensitiveFloats.clear!
import ..Log
using ..Transform
import DataFrames
import Optim

export ElboArgs

"""
Some parameter to a function has invalid values. The message should explain what parameter is
invalid and why.
"""
type InvalidInputError <: Exception
    message::String
end

"""
ElboArgs stores the arguments needed to evaluate the variational objective
function.
"""
type ElboArgs{NumType <: Number}
    S::Int64
    N::Int64

    # The number of components in the point spread function.
    psf_K::Int64
    images::Vector{TiledImage}
    vp::VariationalParams{NumType}
    tile_source_map::Vector{Matrix{Vector{Int}}}
    patches::Matrix{SkyPatch}
    active_sources::Vector{Int}

    # Bivarite normals will not be evaulated at points further than this many
    # standard deviations away from their mean.  See its usage in the ELBO and
    # bivariate normals for details.
    #
    # If this is set to Inf, the bivariate normals will be evaluated at all points
    # irrespective of their distance from the mean.
    num_allowed_sd::Float64

    active_pixels::Vector{ActivePixel}
end


function ElboArgs{NumType <: Number}(
            images::Vector{TiledImage},
            vp::VariationalParams{NumType},
            tile_source_map::Vector{Matrix{Vector{Int}}},
            patches::Matrix{SkyPatch},
            active_sources::Vector{Int};
            psf_K::Int=2,
            num_allowed_sd::Float64=Inf)
    N = length(images)
    S = length(vp)

    @assert psf_K > 0
    @assert length(tile_source_map) == N
    @assert size(patches, 1) == S
    @assert size(patches, 2) == N

    for img in images, tile in img.tiles, ep in tile.epsilon_mat
        if ep <= 0.0
            throw(InvalidInputError(
                "You must set all values of epsilon_mat > 0 for all images included in ElboArgs"
            ))
        end
    end

    active_pixels = ActivePixel[]
    for i in 1:N
        tiles = images[i].tiles
        for j in 1:length(tiles)
            W, H = size(tiles[j].pixels)
            for w in 1:W, h in 1:H
                push!(active_pixels, ActivePixel(i, j, h, w))
            end
        end
    end

    ElboArgs(S, N, psf_K, images, vp, tile_source_map, patches,
             active_sources, num_allowed_sd, ActivePixel[])
end


include("deterministic_vi/elbo_kl.jl")
include("deterministic_vi/source_brightness.jl")
include("deterministic_vi/elbo.jl")
include("deterministic_vi/maximize_elbo.jl")


end
